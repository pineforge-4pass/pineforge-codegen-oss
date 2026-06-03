import pytest
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.symbols import PineType
from pineforge_codegen.errors import CompileError

def _analyze(src: str):
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    return Analyzer(ast).analyze()

# === Task 7: Scope Resolution & Type Inference ===

def test_variable_type_inference():
    ctx = _analyze("//@version=6\nstrategy(\"T\")\nx = 14\n")
    sym = ctx.symbols.resolve("x")
    assert sym is not None
    assert sym.pine_type == PineType.INT

def test_float_inference():
    ctx = _analyze("//@version=6\nstrategy(\"T\")\nx = 3.14\n")
    sym = ctx.symbols.resolve("x")
    assert sym.pine_type == PineType.FLOAT

def test_input_resolves_to_default():
    ctx = _analyze('//@version=6\nstrategy("T")\nlength = input.int(14, "Len")\n')
    sym = ctx.symbols.resolve("length")
    assert sym.is_const is True
    assert sym.const_value == 14

def test_undefined_variable_returns_float():
    """Unknown identifiers (e.g. from skipped enum/type blocks) resolve as FLOAT."""
    ctx = _analyze("//@version=6\nstrategy(\"T\")\nx = y + 1\n")
    sym = ctx.symbols.resolve("x")
    assert sym is not None
    assert sym.pine_type == PineType.FLOAT

def test_builtin_variables_predefined():
    ctx = _analyze("//@version=6\nstrategy(\"T\")\nx = close\n")
    sym = ctx.symbols.resolve("close")
    assert sym is not None
    assert sym.pine_type == PineType.FLOAT

def test_var_declaration():
    ctx = _analyze("//@version=6\nstrategy(\"T\")\nvar float x = 0.0\n")
    sym = ctx.symbols.resolve("x")
    assert sym.is_var is True

def test_function_scope():
    src = '//@version=6\nstrategy("T")\nf(a) =>\n    a + 1\nx = f(5)\n'
    ctx = _analyze(src)
    assert ctx.symbols.resolve("a") is None

def test_func_info_populated():
    src = '//@version=6\nstrategy("T")\nf(a, b) =>\n    a + b\nx = f(1.0, 2.0)\n'
    ctx = _analyze(src)
    assert len(ctx.func_infos) == 1
    fi = ctx.func_infos[0]
    assert fi.name == "f"
    assert fi.return_type == PineType.FLOAT
    assert len(fi.param_types) == 2

# === Task 8: Series Detection, TA Collection, Skip Detection ===

def test_series_detection():
    src = '//@version=6\nstrategy("T")\nbprice = 0.0\nbprice := nz(bprice[1])\n'
    ctx = _analyze(src)
    assert "bprice" in ctx.series_vars

def test_bar_field_series():
    src = '//@version=6\nstrategy("T")\nx = close[1]\n'
    ctx = _analyze(src)
    assert "close" in ctx.series_bar_fields

def test_ta_call_site_collection():
    src = '//@version=6\nstrategy("T")\nx = ta.sma(close, 14)\n'
    ctx = _analyze(src)
    assert len(ctx.ta_call_sites) == 1
    site = ctx.ta_call_sites[0]
    assert site.class_name == "ta::SMA"
    assert "14" in site.ctor_args

def test_ta_multiple_call_sites():
    src = '//@version=6\nstrategy("T")\na = ta.sma(close, 14)\nb = ta.sma(close, 28)\n'
    ctx = _analyze(src)
    assert len(ctx.ta_call_sites) == 2
    assert ctx.ta_call_sites[0].member_name != ctx.ta_call_sites[1].member_name

def test_var_member_collection():
    src = '//@version=6\nstrategy("T")\nvar float x = 0.0\n'
    ctx = _analyze(src)
    assert any(name == "x" for name, _, _ in ctx.var_members)

def test_skip_plot():
    src = '//@version=6\nstrategy("T")\nplot(close)\n'
    ctx = _analyze(src)
    # Should not raise error

def test_unsupported_import():
    with pytest.raises(CompileError) as exc_info:
        _analyze("//@version=6\nimport foo/bar/1\nstrategy(\"T\")\n")
    assert "not supported" in exc_info.value.diagnostics[0].message.lower()

def test_ta_tr_adds_close_series():
    src = '//@version=6\nstrategy("T")\nx = ta.tr\n'
    ctx = _analyze(src)
    assert "close" in ctx.series_bar_fields

def test_fixnan_call_site():
    src = '//@version=6\nstrategy("T")\nx = fixnan(close)\n'
    ctx = _analyze(src)
    assert len(ctx.fixnan_sites) == 1
    assert ctx.fixnan_sites[0].member_name == "_prev_fixnan_1"

def test_strategy_params():
    src = '//@version=6\nstrategy("Test", overlay=true, initial_capital=10000)\n'
    ctx = _analyze(src)
    assert ctx.strategy_params.get("title") == "Test" or "Test" in str(ctx.strategy_params)


def test_rejects_non_v6_version():
    with pytest.raises(CompileError) as exc:
        _analyze('//@version=5\nstrategy("T")\n')
    assert "v6 only" in exc.value.diagnostics[0].message.lower()


def test_requires_version_directive():
    with pytest.raises(CompileError) as exc:
        _analyze('strategy("T")\nx = 1\n')
    assert "version directive" in exc.value.diagnostics[0].message.lower()


# --- TradingView-style input.* compile-time warnings (tv_input_choices) ---


def test_input_source_no_warning_for_builtin_series():
    ctx = _analyze('//@version=6\nstrategy("T")\nx = input.source(close, "t")\n')
    assert not ctx.diagnostics


def test_input_source_warns_unknown_identifier():
    ctx = _analyze('//@version=6\nstrategy("T")\nx = input.source(notaseries, "t")\n')
    assert any("notaseries" in d.message for d in ctx.diagnostics)


def test_input_source_warns_complex_expression():
    ctx = _analyze('//@version=6\nstrategy("T")\nx = input.source(ta.sma(close, 14), "t")\n')
    assert any("not a native chart series" in d.message for d in ctx.diagnostics)


def test_input_timeframe_warns_implausible_string():
    ctx = _analyze(
        '//@version=6\nstrategy("T")\nx = input.timeframe("bogus!!!", "t")\n'
    )
    assert any("timeframe" in d.message.lower() for d in ctx.diagnostics)


def test_input_session_warns_implausible_string():
    ctx = _analyze('//@version=6\nstrategy("T")\nx = input.session("bad", "t")\n')
    assert any("session" in d.message.lower() for d in ctx.diagnostics)


def test_input_string_defval_not_in_options_warns():
    src = '''//@version=6
strategy("T")
x = input.string("b", "t", options=["a", "c"])
'''
    ctx = _analyze(src)
    assert any("options" in d.message.lower() for d in ctx.diagnostics)


def test_input_enum_warns_unknown_member():
    src = '''//@version=6
strategy("T")
enum Signal
    Buy
    Sell
x = input.enum(Signal.Short, "t")
'''
    ctx = _analyze(src)
    assert any("not a member" in d.message for d in ctx.diagnostics)


def test_input_enum_requires_enum_declared_above():
    """Pine: user enum must appear before input.enum(Enum.member, ...)."""
    src = '''//@version=6
strategy("T")
x = input.enum(Signal.Buy, "t")
enum Signal
    Buy
    Sell
'''
    with pytest.raises(CompileError) as exc:
        _analyze(src)
    assert "declared above" in exc.value.diagnostics[0].message.lower()


def test_input_enum_errors_unknown_enum_name():
    with pytest.raises(CompileError) as exc:
        _analyze(
            '//@version=6\nstrategy("T")\n'
            "x = input.enum(UnknownEnum.member, \"t\")\n"
        )
    assert "declared above" in exc.value.diagnostics[0].message.lower()


def test_input_enum_resolves_to_int_const():
    """input.enum(Signal.Buy, ...) is INT with const_value = member index."""
    src = '''//@version=6
strategy("T")
enum Signal
    Buy
    Sell
sig = input.enum(Signal.Buy, "t")
'''
    ctx = _analyze(src)
    sym = ctx.symbols.resolve("sig")
    assert sym is not None
    assert sym.pine_type == PineType.INT
    assert sym.const_value == 0
