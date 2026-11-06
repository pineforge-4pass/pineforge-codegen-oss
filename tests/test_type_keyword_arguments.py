"""Type keywords are call labels without becoming declaration names."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


HEADER = '//@version=6\nstrategy("pf_probe")\n'
TYPE_NAMES = ("int", "float", "bool", "string")


def _transpile(body):
    return transpile(HEADER + body + "\n", filename="probe.pine")


def _error(body):
    with pytest.raises(CompileError) as caught:
        _transpile(body)
    return next(d for d in caught.value.diagnostics if d.level == Level.ERROR)


@pytest.mark.parametrize("value", [
    '"abc"',
    '"abcdefg"',
    'bar_index % 2 == 0 ? "abcd" : "abcdefg"',
    'str.tostring(math.sqrt(number = 4.0))',
])
def test_type_keyword_label_lowers_like_positional(value):
    expected = _transpile(f"pf_value = str.length({value})")
    assert _transpile(f"pf_value = str.length(string = {value})") == expected


@pytest.mark.parametrize("name", TYPE_NAMES)
def test_type_keyword_label_still_requires_a_matching_parameter(name):
    ordinary = _error("pf_value = ta.sma(pf_unknown = close, length = 5)")
    body = f"pf_value = ta.sma({name} = close, length = 5)"
    diagnostic = _error(body)
    assert diagnostic.code == ordinary.code
    assert diagnostic.phase == ordinary.phase
    assert diagnostic.message == ordinary.message.replace("pf_unknown", name)
    assert diagnostic.location.line == 3
    assert diagnostic.location.col == body.index("close") + 1


@pytest.mark.parametrize("name", TYPE_NAMES)
def test_duplicate_type_keyword_label_keeps_the_label_location(name):
    ordinary = _error("pf_value = math.pow(base = 1.0, base = 2.0)")
    body = f"pf_value = math.pow({name} = 1.0, {name} = 2.0)"
    diagnostic = _error(body)
    assert diagnostic.code == ordinary.code
    assert diagnostic.phase == Phase.PARSER
    assert diagnostic.message == ordinary.message.replace("base", name)
    assert diagnostic.hint == ordinary.hint.replace("base", name)
    assert diagnostic.location.line == 3
    assert diagnostic.location.col == body.rindex(name) + 1
    assert diagnostic.location.end_col == body.rindex(name) + 1 + len(name)


@pytest.mark.parametrize("named,positional", [
    ("ta.sma(source = close, length = 5)", "ta.sma(close, 5)"),
    ('input.int(defval = 3, title = "x")', 'input.int(3, "x")'),
    ("math.sqrt(number = 4.0)", "math.sqrt(4.0)"),
])
def test_identifier_keyword_controls_keep_their_emission(named, positional):
    expected = _transpile(f"pf_value = {positional}")
    assert _transpile(f"pf_value = {named}") == expected


@pytest.mark.parametrize("annotation,value", [
    ("string", '"a"'), ("int", "5"), ("float", "5.0"), ("bool", "true"),
])
def test_type_annotations_keep_their_meaning(annotation, value):
    assert _transpile(f"{annotation} pf_value = {value}")


@pytest.mark.parametrize("name", TYPE_NAMES)
def test_type_keyword_is_still_refused_as_a_variable_name(name):
    assert _error(f"{name} = 5").phase == Phase.PARSER


@pytest.mark.parametrize("name", TYPE_NAMES)
def test_type_keyword_is_still_refused_as_a_parameter_name(name):
    assert _error(f"pf_echo({name}) => 1.0").phase == Phase.PARSER


@pytest.mark.parametrize("name", TYPE_NAMES)
def test_type_keyword_parameter_name_with_default_is_still_refused(name):
    control = _error(f"pf_echo({name}) => 1.0")
    body = f"pf_echo({name} = 1) => 1.0"
    diagnostic = _error(body)
    assert diagnostic.code == control.code
    assert diagnostic.phase == control.phase == Phase.PARSER
    assert diagnostic.location.line == control.location.line
    column = body.index("=") + 1
    assert diagnostic.location.col == column
    assert diagnostic.location.end_col == column + len("=")


@pytest.mark.parametrize("call,late_argument", [
    ('str.substring(source = "abc", 1)', "1"),
    ('str.split(string = "abc", ",")', '\",\"'),
    ('str.tonumber(string = "123", 4)', "4"),
])
def test_positional_after_keyword_keeps_its_diagnostic(call, late_argument):
    body = f"pf_value = {call}"
    diagnostic = _error(body)
    assert diagnostic.code == "PF-E2074"
    assert diagnostic.location.line == 3
    assert diagnostic.location.col == body.rindex(late_argument) + 1


def test_type_casts_and_equality_are_not_argument_labels():
    assert _transpile("pf_value = math.max(int(4.0), 2)")
    assert _transpile("pf_value = math.max(float(4), 2.0)")
    assert _transpile("pf_value = bool(4) == true")
    assert _transpile('pf_value = string(4) == "4"')
