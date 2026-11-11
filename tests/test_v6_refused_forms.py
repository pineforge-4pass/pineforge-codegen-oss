"""Version-specific refusals retain exact messages and authored token positions."""

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


COLOR_SCRIPT = """//@version=6
strategy("t", overlay = true)
plot(close, color = color.new(color.red))
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

COLOR_CONTROL = """//@version=6
strategy("t", overlay = true)
plot(close, color = color.new(color.red, 0))
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

FILL_SCRIPT = """//@version=6
strategy("t", overlay = true)
p1 = plot(high)
p2 = plot(low)
fill(p1, p2, transp = 90)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

FILL_CONTROL = """//@version=6
strategy("t", overlay = true)
p1 = plot(high)
p2 = plot(low)
fill(p1, p2, color = color.new(color.blue, 90))
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

TIME_SCRIPT = """//@version=6
strategy("t", overlay = true)
start = input.time(defval = timestamp("UTC", 2023, 1, 1, 0, 0), title = "Start")
plot(time >= start ? close : na)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

TIME_CONTROL = """//@version=6
strategy("t", overlay = true)
start = input.time(defval = timestamp("01 Jan 2023 00:00 +0000"), title = "Start")
plot(time >= start ? close : na)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

WIDTH_SCRIPT = """//@version=6
strategy("t", overlay = true)
plot(close, linewidth = 0)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

WIDTH_CONTROL = """//@version=6
strategy("t", overlay = true)
plot(close, linewidth = 1)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

NAME_SCRIPT = """//@version=6
strategy("t", overlay = true)
range = high - low
plot(range)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

NAME_CONTROL = """//@version=6
strategy("t", overlay = true)
rng = high - low
plot(rng)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

MESSAGES = {
    "PF-E2088": (
        "color.new() needs its transparency argument in Pine v6: "
        "write color.new(<color>, <transparency>), "
        "for example color.new(color.red, 0)."
    ),
    "PF-E2089": (
        'fill() has no argument named "transp" in Pine v6: '
        "put the transparency into the color, "
        "for example color = color.new(color.blue, 90)."
    ),
    "PF-E2090": (
        "input.time() needs a constant default in Pine v6: "
        "timestamp() with a time zone and calendar numbers is not a constant. "
        'Use the date-string form, for example timestamp("01 Jan 2023 00:00 +0000").'
    ),
    "PF-E2091": (
        "plot() cannot have a linewidth below 1 in Pine v6: "
        "use a value of 1 or greater."
    ),
    "PF-E2092": (
        '"range" cannot be used as a variable or function name in Pine v6: '
        "use another name."
    ),
}


def _source(body):
    """Place the provided text after a fixed two-line v6 declaration."""
    return '//@version=6\nstrategy("Forms")\n' + body + "\n"


def _assert_refused(source, code, line, col):
    """Check a literal expected message and position without deriving either."""
    message = MESSAGES[code]
    expected = f"forms.pine:{line}:{col}: {message}"
    with pytest.raises(CompileError, match="^" + re.escape(expected) + "$") as caught:
        transpile(source, filename="forms.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.args == {}
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "forms.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


def _older_library(body):
    """Translate a v5 definition through a v6 caller without native inputs."""
    library = '//@version=5\nlibrary("Older forms")\nexport value(float x) =>\n' + body
    caller = _source("import local/Forms/1 as forms\nplot(forms.value(close))")
    return transpile(caller, libraries={"local/Forms/1": library})


def test_color_reported_call():
    """A color-only call inside a plot points to the color namespace token."""
    _assert_refused(COLOR_SCRIPT, "PF-E2088", 3, 21)


def test_color_reported_control():
    """The full strategy translates when color.new supplies zero transparency."""
    transpile(COLOR_CONTROL)


def test_color_in_background():
    """A nested background color call cannot omit transparency."""
    _assert_refused(_source("bgcolor(color.new(color.blue))"), "PF-E2088", 3, 9)


def test_color_in_assignment():
    """An ordinary color assignment cannot omit transparency."""
    _assert_refused(_source("shade = color.new(color.green)"), "PF-E2088", 3, 9)


def test_color_in_function():
    """An uncalled helper body still checks color transparency."""
    _assert_refused(_source("shade() =>\n    color.new(color.orange)"), "PF-E2088", 4, 5)


def test_color_in_branch():
    """A color declaration inside a conditional retains the call position."""
    _assert_refused(
        _source("if close > open\n    tint = color.new(color.yellow)"),
        "PF-E2088", 4, 12,
    )


def test_color_with_named_color_only():
    """Naming the sole color argument does not supply transparency."""
    _assert_refused(
        _source("tint = color.new(color = color.blue)"), "PF-E2088", 3, 8,
    )


def test_color_in_parameter_default():
    """A call stored in a parameter default still requires transparency."""
    _assert_refused(
        _source("shade(color tint = color.new(color.blue)) => tint"),
        "PF-E2088", 3, 20,
    )


def test_color_with_named_transparency():
    """An explicit named transparency remains accepted."""
    transpile(_source("tint = color.new(color.red, transp = 0)"))


def test_color_with_reordered_keywords():
    """Named transparency remains accepted before a named color."""
    transpile(_source("tint = color.new(transp = 15, color = color.orange)"))


def test_color_with_variable_transparency():
    """A variable second argument satisfies the transparency requirement."""
    transpile(_source("opacity = input.int(27)\ntint = color.new(color.lime, opacity)"))


def test_color_with_na_transparency():
    """An explicit na transparency is outside the missing-argument refusal."""
    transpile(_source("tint = color.new(color.red, na)"))


def test_color_in_older_library():
    """A v5 library retains its one-argument color.new lowering."""
    _older_library("    tint = color.new(color.blue)\n    x\n")


def test_fill_reported_call():
    """A fill transp argument points to its keyword rather than its value."""
    _assert_refused(FILL_SCRIPT, "PF-E2089", 5, 14)


def test_fill_reported_control():
    """The full strategy translates with transparency encoded in its color."""
    transpile(FILL_CONTROL)


def test_user_function_named_fill_is_not_the_builtin():
    """A script's own fill function translates, as it does without the refusals."""
    transpile(_source(
        "fill(a, b, transp) => a + b + transp\n"
        "x = fill(1, 2, transp = 3)\nplot(x)"
    ))


def test_fill_transparency_not_last():
    """The removed fill keyword is refused even before the color argument."""
    _assert_refused(
        _source(
            "upper = plot(high)\nlower = plot(low)\n"
            "fill(upper, lower, transp = 50, color = color.green)"
        ),
        "PF-E2089", 5, 20,
    )


def test_fill_transparency_first():
    """A fill call with only named arguments still locates transp."""
    _assert_refused(
        _source(
            "top = plot(high)\nbottom = plot(low)\n"
            "fill(transp = 20, plot1 = top, plot2 = bottom)"
        ),
        "PF-E2089", 5, 6,
    )


def test_fill_in_branch():
    """A conditional visual call still validates its transp keyword."""
    _assert_refused(
        _source(
            "a = plot(high)\nb = plot(low)\n"
            "if close > open\n    fill(a, b, transp = 35)"
        ),
        "PF-E2089", 6, 16,
    )


def test_fill_in_function():
    """An uncalled helper still checks a removed fill argument."""
    _assert_refused(
        _source("shade() =>\n    fill(1, 2, transp = 65)\n    0"),
        "PF-E2089", 4, 16,
    )


def test_fill_as_argument():
    """A fill call nested under another skipped visual call is inspected."""
    _assert_refused(
        _source("plot(fill(1, 2, transp = 45))"), "PF-E2089", 3, 17,
    )


def test_fill_without_transparency():
    """A fill without transp keeps its existing translation."""
    transpile(_source("a = plot(high)\nb = plot(low)\nfill(a, b)"))


def test_other_visual_transparency():
    """The fill refusal does not reject transp on another visual function."""
    transpile(_source("bgcolor(color.blue, transp = 50)"))


def test_other_color_transparency():
    """The fill refusal does not reject color.rgb transparency."""
    transpile(_source("tint = color.rgb(12, 34, 56, transp = 50)"))


def test_fill_in_older_library():
    """A v5 library retains a fill call with named transparency."""
    _older_library("    fill(1, 2, transp = 50)\n    x\n")


def test_time_reported_call():
    """A calendar default points to the timestamp identifier."""
    _assert_refused(TIME_SCRIPT, "PF-E2090", 3, 29)


def test_time_reported_control():
    """The full strategy translates with a constant date-string timestamp."""
    transpile(TIME_CONTROL)


def test_user_function_named_timestamp_is_not_the_builtin():
    """A script's own timestamp function translates, as it does without the refusals."""
    transpile(_source(
        "timestamp(zone, a, b, c, d) => a + b\n"
        'cutoff = input.time(timestamp("UTC", 2, 3, 4, 5), "cutoff")\n'
        "plot(cutoff)"
    ))


def test_time_numeric_calendar():
    """An unzoned calendar default stays accepted: only the zoned form was measured."""
    transpile(
        _source('cutoff = input.time(defval = timestamp(2024, 2, 3, 4, 5), title = "Cutoff")'),
    )


def test_time_positional_default():
    """A positional unzoned calendar default stays accepted."""
    transpile(
        _source('cutoff = input.time(timestamp(2025, 6, 7, 8, 9), "Cutoff")'),
    )


def test_time_timezone_first():
    """An explicit timezone does not make calendar numbers constant."""
    _assert_refused(
        _source('cutoff = input.time(timestamp("Asia/Tokyo", 2024, 2, 3, 4, 5))'),
        "PF-E2090", 3, 21,
    )


def test_time_timezone_keyword():
    """A timezone keyword selects the same zoned form as a leading zone string."""
    _assert_refused(
        _source(
            'cutoff = input.time(timestamp(timezone = "UTC", year = 2024, month = 2, '
            "day = 3, hour = 4, minute = 5))"
        ),
        "PF-E2090", 3, 21,
    )


def test_time_named_calendar_fields():
    """Unzoned calendar fields passed by name stay accepted."""
    transpile(
        _source(
            "cutoff = input.time(defval = timestamp("
            "year = 2024, month = 2, day = 3, hour = 4, minute = 5))"
        ),
    )


def test_time_numeric_fixture_default():
    """The numeric metadata fixture's unzoned default stays accepted."""
    fixture = Path(__file__).parent / "fixtures" / "input_metadata_numeric.pine"
    parts_time = fixture.read_text(encoding="utf-8").splitlines()[24]
    transpile(_source(parts_time + "\nplot(close)"))


def test_time_nested_visual_argument():
    """A time input inside a skipped visual argument is still validated."""
    _assert_refused(
        _source('plot(input.time(timestamp("UTC", 2024, 2, 3, 4, 5)))'),
        "PF-E2090", 3, 17,
    )


def test_time_in_function():
    """An uncalled function body still validates its time input default."""
    _assert_refused(
        _source('cutoff() =>\n    input.time(timestamp("UTC", 2024, 2, 3, 4, 5))'),
        "PF-E2090", 4, 16,
    )


def test_time_in_branch():
    """A time input declaration inside a branch still checks its default."""
    _assert_refused(
        _source('if close > open\n    cutoff = input.time(timestamp("UTC", 2024, 2, 3, 4, 5))'),
        "PF-E2090", 4, 25,
    )


def test_time_integer_default():
    """An integer timestamp literal is a permitted time default."""
    transpile(_source("cutoff = input.time(defval = 1672531200000)"))


def test_time_positional_string_default():
    """A positional date-string timestamp remains a permitted time default."""
    transpile(_source('cutoff = input.time(timestamp("01 Jan 2023 00:00 +0000"))'))


def test_time_named_string_default():
    """A timestamp dateString keyword remains a permitted time default."""
    transpile(_source(
        'cutoff = input.time(defval = timestamp(dateString = "01 Jan 2023 00:00 +0000"))'
    ))


def test_calendar_timestamp_without_input():
    """A calendar timestamp outside input.time retains its lowering."""
    transpile(_source("cutoff = timestamp(2024, 2, 3, 4, 5)\nplot(cutoff)"))


def test_time_in_older_library():
    """A v5 library retains a calendar timestamp default for input.time."""
    _older_library('    cutoff = input.time(timestamp("UTC", 2024, 2, 3, 4, 5))\n    x\n')


def test_width_reported_call():
    """A zero plot width points to the integer literal."""
    _assert_refused(WIDTH_SCRIPT, "PF-E2091", 3, 25)


def test_width_reported_control():
    """The full strategy translates with a plot width of one."""
    transpile(WIDTH_CONTROL)


def test_user_function_named_plot_is_not_the_builtin():
    """A script's own plot function translates, as it does without the refusals."""
    transpile(_source(
        "plot(a, b, c, d) => a + b + c + d\n"
        "x = plot(1, 2, 3, 0)"
    ))


def test_width_negative_literal():
    """A negative plot width points to its minus token."""
    _assert_refused(_source("plot(open, linewidth = -1)"), "PF-E2091", 3, 24)


def test_width_fourth_positional_argument():
    """The fourth positional plot argument binds linewidth."""
    _assert_refused(
        _source('plot(high, "High", color.green, 0)'), "PF-E2091", 3, 33,
    )


def test_width_in_branch():
    """A conditional plot still checks a negative integer width."""
    _assert_refused(
        _source("if close > open\n    plot(low, linewidth = -3)"), "PF-E2091", 4, 27,
    )


def test_width_in_function():
    """An uncalled function body still checks literal plot widths."""
    _assert_refused(
        _source("draw() =>\n    plot(high, linewidth = 0)\n    0"),
        "PF-E2091", 4, 28,
    )


def test_width_as_argument():
    """A plot nested under another visual call still checks its width."""
    _assert_refused(
        _source("bgcolor(plot(close, linewidth = 0))"), "PF-E2091", 3, 33,
    )


def test_width_input():
    """A plot width read from an input stays outside the literal check."""
    transpile(_source("width = input.int(0)\nplot(close, linewidth = width)"))


def test_width_larger_literal():
    """An integer width greater than one remains accepted."""
    transpile(_source("plot(close, linewidth = 4)"))


def test_width_expression():
    """An arithmetic width expression stays outside the literal check."""
    transpile(_source("plot(close, linewidth = 0 + 0)"))


def test_other_function_linewidth():
    """The literal plot rule leaves another function's linewidth alone."""
    transpile(_source("hline(0, linewidth = 0)"))


def test_width_in_older_library():
    """A v5 library retains a zero plot width."""
    _older_library("    plot(x, linewidth = 0)\n    x\n")


def test_name_reported_declaration():
    """A plain declaration of range points to its identifier."""
    _assert_refused(NAME_SCRIPT, "PF-E2092", 3, 1)


def test_name_persistent_declaration():
    """A persistent range declaration points past the var keyword."""
    _assert_refused(_source("var range = 1.0"), "PF-E2092", 3, 5)


def test_name_intrabar_declaration():
    """An intrabar range declaration points past the varip keyword."""
    _assert_refused(_source("varip range = 2.0"), "PF-E2092", 3, 7)


def test_name_typed_declaration():
    """A typed range declaration points past its type keyword."""
    _assert_refused(_source("float range = 3.0"), "PF-E2092", 3, 7)


def test_name_function_declaration():
    """A function named range points to its declaration identifier."""
    _assert_refused(_source("range(x) => x + 1"), "PF-E2092", 3, 1)


def test_name_method_declaration():
    """A method named range points past the method keyword."""
    _assert_refused(_source("method range(float x) => x + 2"), "PF-E2092", 3, 8)


def test_name_first_tuple_binding():
    """The first tuple binding is checked as a variable declaration."""
    _assert_refused(
        _source("pair() => [high, low]\n[range, other] = pair()"),
        "PF-E2092", 4, 2,
    )


def test_name_second_tuple_binding():
    """A later tuple binding points to that identifier rather than the bracket."""
    _assert_refused(
        _source("pair() => [open, close]\n[other, range] = pair()"),
        "PF-E2092", 4, 9,
    )


def test_name_tuple_in_function_body():
    """A tuple name inside an uncalled function retains its token position."""
    _assert_refused(
        _source("pair() => [high, low]\nspan() =>\n    [other, range] = pair()\n    other"),
        "PF-E2092", 5, 13,
    )


def test_name_in_function_body():
    """A local range declaration inside an uncalled function is checked."""
    _assert_refused(
        _source("span() =>\n    range = high - low\n    range"),
        "PF-E2092", 4, 5,
    )


def test_name_in_branch():
    """A conditional declaration of range retains its identifier position."""
    _assert_refused(
        _source("if close > open\n    range = high - low"),
        "PF-E2092", 4, 5,
    )


def test_name_with_extra_spacing():
    """The name position follows authored spacing rather than a fixed offset."""
    _assert_refused(_source("var   float   range = 4.0"), "PF-E2092", 3, 15)


def test_name_reported_control():
    """The full strategy translates after renaming the reserved variable."""
    transpile(NAME_CONTROL)


def test_name_similar_plural():
    """A variable named ranges remains an ordinary declaration."""
    transpile(_source("ranges = high - low\nplot(ranges)"))


def test_name_similar_suffix():
    """A variable named range_1 remains an ordinary declaration."""
    transpile(_source("range_1 = high - low\nplot(range_1)"))


def test_name_similar_prefix():
    """A variable named my_range remains an ordinary declaration."""
    transpile(_source("my_range = high - low\nplot(my_range)"))


def test_name_parameter():
    """A function parameter named range remains accepted."""
    transpile(_source("increment(float range) => range + 1\nplot(increment(close))"))


def test_name_field():
    """A user type field named range remains accepted."""
    transpile(_source("type Span\n    float range = 0\nitem = Span.new()"))


def test_name_member_access():
    """Reading a range member remains accepted."""
    transpile(_source("type Span\n    float range = 0\nitem = Span.new()\nplot(item.range)"))


def test_name_in_older_library():
    """A v5 library retains a local variable named range."""
    _older_library("    range = x + 2\n    range\n")
