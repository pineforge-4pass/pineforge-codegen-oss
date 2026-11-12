"""History of a string helper local spelled like a price series stays refused.

A requested helper's non-``var`` string local named ``open``, ``high``,
``low``, ``close``, ``hl2``, ``hlc3``, ``hlcc4``, ``ohlc4`` or ``volume``
whose history it reads keeps the base commit's PF-E3011 refusal at that read.
Every other built-in-named local, the numeric locals of those names and their
string locals without a history read keep the neutral control's C++. A string
global, a string parameter and a library local of those names, read with
history in a requested helper, are not such locals and keep what the base
commit gives. Sources come from the scope comparison's builders. The base rows
read the base commit's outcome (its refusal, or its C++ where it accepts the
script) through the glue run on that commit's tree; every other row runs this
checkout's transpiler only, and no row compiles C++.

The scope and helper test files, which this module imports on load, import
``PRICE_FIELD_NAMES`` and ``assert_price_refusal`` inside their test bodies.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._e2e import reference_codegen, transpile_json
from tests._older_codegen import with_utf16_units
from tests._pine_libraries import Layout
from tests.test_builtin_named_locals_scope import (
    _LOCAL_NAMES, _chart_local_source, _normalize_local, _program,
    _requested_source, _variant_source,
)
from tests.test_helper_builtin_named_locals import CPP_REFERENCE


# Written out by hand, apart from the product's own table of these names.
PRICE_FIELD_NAMES = ("open", "high", "low", "close", "hl2", "hlc3", "hlcc4", "ohlc4", "volume")
REFUSAL_MESSAGE = (
    "request.security helper-parameter history currently requires "
    "a direct OHLC/time bar-series binding"
)
# The shapes that read the local's history inside a requested helper.
_HISTORY_SHAPES = ("typed", "nested", "two-sites")


def assert_price_refusal(source, **options):
    """Assert that ``transpile(source, **options)`` is refused exactly as the
    history of a price-named string helper local is; return the diagnostic."""
    with pytest.raises(CompileError) as caught:
        transpile(source, **options)
    error = caught.value
    assert len(error.diagnostics) == 1, error.diagnostics
    diagnostic = error.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.code == "PF-E3011"
    assert diagnostic.hint is None
    assert diagnostic.message == REFUSAL_MESSAGE
    assert "__pfhist" not in str(error)
    assert "__pfhist" not in diagnostic.message
    return diagnostic


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip(f"The C++ reference commit {CPP_REFERENCE} is unavailable")
    return tree


def _reference_refusal(source, reference, tmp_path):
    """The reference commit's refusal of ``source``, read through its glue,
    as (severity, code, message, line, column) per diagnostic."""
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    result = transpile_json(pine, root=reference)
    assert not result["ok"], result
    return [
        (entry["severity"], entry["code"], entry["message"], entry["line"], entry["col"])
        for entry in result["diagnostics"]
    ]


def _reference_outcome(source, reference, tmp_path, libraries=None):
    """The reference commit's outcome for ``source``, read through its glue:
    ``("accepted", cpp)``, or ``("refused", entries)`` with one (severity,
    code, message, line, column) entry per diagnostic. ``libraries`` maps the
    import paths of the script to library text."""
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    extra_env = {}
    if libraries is not None:
        layout = Layout(tmp_path / "library_env")
        pins = {
            path: layout.add_library(path, text.encode("utf-8"))
            for path, text in libraries.items()
        }
        layout.add_probe("strategy", source.encode("utf-8"), pins)
        extra_env = layout.env
    result = transpile_json(pine, root=reference, extra_env=extra_env)
    if result["ok"]:
        return "accepted", result["cpp"]
    return "refused", [
        (entry["severity"], entry["code"], entry["message"], entry["line"], entry["col"])
        for entry in result["diagnostics"]
    ]


def _glue_entry(diagnostic):
    """A diagnostic as the glue reports it: (severity, code, message, line,
    column), with the hint, if any, joined to the message."""
    location = diagnostic.location
    message = diagnostic.message
    if diagnostic.hint:
        message += " — " + diagnostic.hint
    return (
        diagnostic.level.value, diagnostic.code, message,
        location.line if location else 1, location.col if location else 1,
    )


def _assert_keeps_the_base_outcome(source, reference, tmp_path, libraries=None,
                                   string_join_fixed=False):
    """Assert that this checkout gives ``source`` exactly what the reference
    commit gives it: the same C++ where the reference accepts the script, the
    same diagnostics where it refuses it. ``string_join_fixed`` repairs, in the
    reference's C++, the one join of a string local with its previous value
    that the reference wrapped in std::to_string (issue #354); that site is
    then the whole difference that is allowed, with one other: the reference
    counted ``str.length`` in bytes, through no unit helper."""
    expected = _reference_outcome(source, reference, tmp_path, libraries)
    if string_join_fixed and expected[0] == "accepted":
        from tests.test_string_local_history_concat import with_string_join_fixed

        expected = ("accepted", with_string_join_fixed(expected[1]))
    if expected[0] == "accepted":
        expected = ("accepted", with_utf16_units(expected[1]))
    try:
        cpp = transpile(source, libraries=libraries)
    except CompileError as error:
        # The script reaches the code generator, where the rule is, and is not
        # stopped by an earlier phase.
        errors = {d.phase for d in error.diagnostics if d.level == Level.ERROR}
        assert errors == {Phase.CODEGEN}, error.diagnostics
        actual = ("refused", [_glue_entry(d) for d in error.diagnostics])
    else:
        assert cpp.strip()
        actual = ("accepted", cpp)
    assert actual[0] == expected[0], (actual[0], expected[0])
    assert actual[1] == expected[1]


@pytest.mark.parametrize("name", PRICE_FIELD_NAMES)
def test_price_named_string_local_history_keeps_the_base_refusal(name, reference, tmp_path):
    source = _variant_source(name, "typed", string=True)
    diagnostic = assert_price_refusal(source)
    location = diagnostic.location
    # The glue reports no phase; assert_price_refusal pins this one's.
    assert _reference_refusal(source, reference, tmp_path) == [(
        diagnostic.level.value, diagnostic.code, diagnostic.message,
        location.line, location.col,
    )]


@pytest.mark.parametrize("name", PRICE_FIELD_NAMES)
def test_price_named_string_local_positional_history_keeps_the_base_refusal(
    name, reference, tmp_path,
):
    # The history is read as the argument of a call, directly in the helper.
    source = _requested_source(
        f"    string {name} = str.tostring(close)\n"
        f"    bb = str.length({name}[1])\n    bb + 1\n",
        string=False,
    )
    diagnostic = assert_price_refusal(source)
    location = diagnostic.location
    assert _reference_refusal(source, reference, tmp_path) == [(
        diagnostic.level.value, diagnostic.code, diagnostic.message,
        location.line, location.col,
    )]


# The refusal is keyed on a non-``var`` string local declared in the helper
# whose authored spelling is a price name. A string global, a string parameter
# and a library local of those spellings are other bindings: whatever the base
# commit gives them, they keep.
def _string_global_source(name):
    return _requested_source(
        f"    bb = {name}[1]\n    str.length(bb)\n", string=False,
        prelude=f"string {name} = str.tostring(close)\n",
    )


def _string_parameter_source(name):
    return _program(
        f"h({name}) =>\n    bb = {name}[1]\n    str.length(bb)\n",
        'v = request.security(syminfo.tickerid, "60", h(str.tostring(close)))\n'
        "plot(v)\n",
        prelude="n = input.int(3)\nz = close[n]\n",
    )


_LIBRARY_PATH = "pftest/Lib/1"
_LIBRARY_SCRIPT = (
    '//@version=6\nstrategy("p")\nimport pftest/Lib/1 as L\n'
    "n = input.int(3)\nz = close[n]\n"
    'v = request.security(syminfo.tickerid, "60", L.f(close))\nplot(v)\n'
)


def _library_text(name):
    return (
        '//@version=6\nlibrary("Lib")\n'
        f"export f(float x) =>\n    string {name} = str.tostring(x)\n"
        f"    bb = {name}[1]\n    str.length({name} + bb)\n"
    )


@pytest.mark.parametrize("name", PRICE_FIELD_NAMES)
def test_price_named_string_global_history_keeps_the_base_outcome(name, reference, tmp_path):
    _assert_keeps_the_base_outcome(_string_global_source(name), reference, tmp_path)


@pytest.mark.parametrize("name", PRICE_FIELD_NAMES)
def test_price_named_string_parameter_history_keeps_the_base_outcome(name, reference, tmp_path):
    _assert_keeps_the_base_outcome(_string_parameter_source(name), reference, tmp_path)


@pytest.mark.parametrize("name", PRICE_FIELD_NAMES)
def test_price_named_string_library_local_history_keeps_the_base_outcome(
    name, reference, tmp_path,
):
    _assert_keeps_the_base_outcome(
        _LIBRARY_SCRIPT, reference, tmp_path,
        libraries={_LIBRARY_PATH: _library_text(name)},
        string_join_fixed=True,
    )


@pytest.mark.parametrize("name,shape", [
    pytest.param(name, shape, id=f"{name}-{shape}")
    for name in PRICE_FIELD_NAMES for shape in ("nested", "two-sites")
])
def test_price_named_string_local_history_in_requested_helper_is_refused(name, shape):
    source = _variant_source(name, shape, string=True)
    diagnostic = assert_price_refusal(source)
    read = f"{name}[1]"
    reads = [
        (number, line) for number, line in enumerate(source.splitlines(), start=1)
        if read in line
    ]
    assert reads, source
    number, line = reads[0]
    assert diagnostic.location.line == number
    # The refusal is located at the history operator of that read.
    assert diagnostic.location.col == line.index(read) + len(name) + 1


# Every other name with history, the nine as numeric locals, and the nine as
# string locals without a history read.
_UNREFUSED_ROWS = [
    pytest.param(name, shape, True, id=f"string-{shape}-{name}")
    for shape in _HISTORY_SHAPES
    for name in _LOCAL_NAMES if name not in PRICE_FIELD_NAMES
] + [
    pytest.param(name, shape, False, id=f"numeric-{shape}-{name}")
    for shape in _HISTORY_SHAPES for name in PRICE_FIELD_NAMES
] + [
    pytest.param(name, "history-free", True, id=f"string-history-free-{name}")
    for name in PRICE_FIELD_NAMES
]


@pytest.mark.parametrize("name,shape,string", _UNREFUSED_ROWS)
def test_refusal_is_not_widened_beyond_price_named_string_history(name, shape, string):
    control = transpile(_variant_source("aa", shape, string=string))
    cpp = transpile(_variant_source(name, shape, string=string))
    assert control.strip()
    assert _normalize_local(cpp, name, control) == control


def _string_source(name, shape):
    if shape == "chart":
        return _chart_local_source(name, string=True)
    return _variant_source(name, shape, string=True)


@pytest.mark.parametrize("name,shape", [
    pytest.param(name, shape, id=f"{name}-{shape}")
    for name in PRICE_FIELD_NAMES for shape in ("reassigned", "chart")
] + [
    pytest.param(name, shape, id=f"{name}-{shape}")
    for name in ("second", "time") for shape in ("reassigned", "chart", "history-free")
])
def test_refusal_stays_narrow_for_shapes_the_base_accepted(name, shape):
    cpp = transpile(_string_source(name, shape))
    assert isinstance(cpp, str) and cpp.strip()
