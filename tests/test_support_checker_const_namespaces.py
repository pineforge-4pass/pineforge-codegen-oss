"""Loud-rejection sweep for constant-only namespaces (audit items A1-A3, A6).

Constant-namespace members (plot.style_*, text.align_*, shape.*, barmerge.*,
alert.freq_*, ...) are only legitimate as arguments to parse-and-skip visual
calls, inside the strategy() declaration, or (barmerge.*) as request.security
gaps/lookahead values. As FREE EXPRESSIONS they used to fall through codegen
to ``std::string("<member>")`` while the analyzer typed them INT — a silent
mismatch. They must now reject with a clear CompileError, while the
argument-context uses keep transpiling.
"""
import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

PRELUDE = '//@version=6\nstrategy("T")\n'


# ---------------------------------------------------------------------------
# Free-expression reads must reject
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr", [
    "plot.style_line + 1",
    "extend.both",
    "font.family_monospace",
    "hline.style_dashed",
    "location.belowbar",
    "scale.right",
    "shape.triangleup",
    "text.align_left",
    "xloc.bar_index",
    "yloc.price",
])
def test_const_namespace_free_expression_rejected(expr):
    ns = expr.split(".")[0]
    with pytest.raises(CompileError, match=ns):
        transpile(PRELUDE + f"x = {expr}\n")


def test_const_namespace_reassignment_rejected():
    src = PRELUDE + 'var s = "a"\ns := text.align_left\n'
    with pytest.raises(CompileError, match="text.align_left"):
        transpile(src)


@pytest.mark.parametrize("expr", [
    "barmerge.gaps_on",
    "barmerge.lookahead_off",
])
def test_barmerge_free_expression_rejected(expr):
    with pytest.raises(CompileError, match="barmerge"):
        transpile(PRELUDE + f"x = {expr}\n")


def test_alert_freq_free_expression_rejected():
    with pytest.raises(CompileError, match="alert.freq_once_per_bar"):
        transpile(PRELUDE + "x = alert.freq_once_per_bar\n")


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
    with pytest.raises(CompileError, match="export"):
        transpile(src)
