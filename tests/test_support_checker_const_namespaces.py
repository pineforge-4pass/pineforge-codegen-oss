"""Loud-rejection sweep for constant-only namespaces (audit items A1-A3, A6).

Unlowered constant domains keep their existing explicit refusal, while
inventoried string values are ordinary expressions. Shape constants now share
their exact string values and types across value and visual-argument contexts.
``alert.freq_*`` are also strings:
TradingView gives them const string values ("all", "once_per_bar",
"once_per_bar_close"; tests/test_e2e_alert_freq_values.py), so they are
ordinary values. ``barmerge.*`` are another: a script may hold and compare
them (on 1, off 0), and a request reads its gaps and lookahead
as the constants written.
"""
import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

PRELUDE = '//@version=6\nstrategy("T")\n'


# ---------------------------------------------------------------------------
# Free-expression reads follow their value contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    ("plot.style_line + 1", None),
    ("extend.both", None),
    ("font.family_monospace", None),
    ("hline.style_dashed", None),
    ("location.belowbar", None),
    ("scale.right", None),
    ("shape.triangleup", "shape_triangle_up"),
    ("text.align_left", None),
    ("xloc.bar_index", None),
    ("yloc.price", None),
])
def test_const_namespace_free_expression_contract(expr, expected):
    ns = expr.split(".")[0]
    if expected is not None:
        cpp = transpile(PRELUDE + f"x = {expr}\n")
        assert f'x = std::string("{expected}");' in cpp
        return
    with pytest.raises(CompileError, match=ns):
        transpile(PRELUDE + f"x = {expr}\n")


def test_const_namespace_reassignment_rejected():
    src = PRELUDE + 'var s = "a"\ns := text.align_left\n'
    with pytest.raises(CompileError, match="text.align_left"):
        transpile(src)


@pytest.mark.parametrize("expr, value", [
    ("barmerge.gaps_on", "1"),
    ("barmerge.lookahead_off", "0"),
])
def test_barmerge_free_expression_has_its_value(expr, value):
    """A barmerge constant is a value a script may hold and compare
    (TradingView/Request/3's ``var gapStrategy = gaps ? barmerge.gaps_on :
    barmerge.gaps_off``; ``fixtures/tail_f_tv/barmerge_values``).
    A request's gaps and lookahead still read the constant as written."""
    cpp = transpile(PRELUDE + f"x = {expr}\n")
    assert f"x = {value};" in cpp


def test_barmerge_unknown_member_rejected():
    with pytest.raises(CompileError, match="is not a barmerge constant"):
        transpile(PRELUDE + "x = barmerge.gaps_maybe\n")


def test_alert_freq_free_expression_has_its_value():
    cpp = transpile(PRELUDE + "x = alert.freq_once_per_bar\n")
    assert 'std::string("once_per_bar")' in cpp


@pytest.mark.parametrize("member", ["bg_color", "fg_color"])
def test_chart_colors_warn_not_rejected(member):
    # Cosmetic chart-theme reads: no backtest-logic effect. Transpile (no-op),
    # not rejected; codegen emits a default color.
    transpile(PRELUDE + f"c = chart.{member}\n")


# ---------------------------------------------------------------------------
# Argument-context uses must keep transpiling
# ---------------------------------------------------------------------------

def test_plot_with_style_constant_still_transpiles():
    transpile(PRELUDE + "plot(close, style=plot.style_line)\n")


def test_plotshape_with_constants_still_transpiles():
    transpile(
        PRELUDE
        + "plotshape(close > open, style=shape.triangleup, "
        "location=location.belowbar, size=size.small)\n"
    )


def test_hline_with_style_constant_still_transpiles():
    transpile(PRELUDE + "hline(50, linestyle=hline.style_dashed)\n")


def test_label_new_with_text_constants_still_transpiles():
    transpile(
        PRELUDE
        + 'label.new(bar_index, high, "x", xloc=xloc.bar_index, '
        "yloc=yloc.price, textalign=text.align_left)\n"
    )


def test_alert_with_freq_constant_still_transpiles():
    transpile(PRELUDE + 'alert("msg", alert.freq_once_per_bar)\n')


def test_strategy_decl_with_scale_constant_still_transpiles():
    transpile('//@version=6\nstrategy("T", scale=scale.right)\nplot(close)\n')


def test_request_security_barmerge_kwargs_still_transpile():
    src = PRELUDE + (
        'htf = request.security(syminfo.tickerid, "60", close, '
        "gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)\n"
    )
    transpile(src)


def test_request_security_lookahead_on_now_supported():
    # lookahead_on is engine-supported: it transpiles (no CompileError) and
    # registers the HTF request with the lookahead flag set true. The per-kwarg
    # value-shape validation still rejects non-barmerge values (see
    # test_request_security_bad_gaps_still_rejected).
    src = PRELUDE + (
        'htf = request.security(syminfo.tickerid, "60", close, '
        "lookahead=barmerge.lookahead_on)\n"
    )
    out = transpile(src)
    assert "input_tf_, true, false)" in out


def test_request_security_bad_gaps_still_rejected():
    src = PRELUDE + (
        'htf = request.security(syminfo.tickerid, "60", close, gaps=close)\n'
    )
    with pytest.raises(CompileError, match="gaps"):
        transpile(src)


# ---------------------------------------------------------------------------
# export keyword (A6)
# ---------------------------------------------------------------------------

def test_export_in_expression_rejected():
    with pytest.raises(CompileError, match="export"):
        transpile(PRELUDE + "x = export + 1\n")


def test_export_function_definition_rejected():
    src = PRELUDE + "export f(float x) =>\n    x * 2\nplot(close)\n"
    with pytest.raises(CompileError, match="export") as caught:
        transpile(src)
    assert caught.value.diagnostics[0].location.line == 3
    assert caught.value.diagnostics[0].location.col == 1
