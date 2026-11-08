"""History of a string helper local spelled like a price series stays refused.

A requested helper's non-``var`` string local named ``open``, ``high``,
``low``, ``close``, ``hl2``, ``hlc3``, ``hlcc4``, ``ohlc4`` or ``volume``
whose history it reads keeps the base commit's PF-E3011 refusal at that read.
Every other built-in-named local, the numeric locals of those names and their
string locals without a history read keep the neutral control's C++. Sources
come from the scope comparison's builders. The base rows read the base
commit's refusal through the glue run on that commit's tree; every other row
runs this checkout's transpiler only, and no row compiles C++.

Other test files import ``PRICE_FIELD_NAMES`` and ``assert_price_refusal``
inside their test bodies, since this module imports the scope file on load.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._e2e import reference_codegen, transpile_json
from tests.test_builtin_named_locals_scope import (
    _LOCAL_NAMES, _chart_local_source, _normalize_local, _variant_source,
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
