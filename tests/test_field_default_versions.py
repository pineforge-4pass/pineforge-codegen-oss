"""Type field default refusals follow the declaration's Pine version."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import reference_codegen, transpile_json
from tests._pine_libraries import Layout
from tests.test_field_default_refusal import CPP_REFERENCE, _CASES


LIBRARY = "example/Defaults/1"
_V5_CASES = [
    pytest.param(
        case.values[0].replace("//@version=6", "//@version=5", 1),
        id=case.id,
    )
    for case in _CASES
]
_LIBRARY_CASES = [
    pytest.param(
        "export type T\n    array<int> values = array.new<int>()\n",
        id="array_constructor",
    ),
    pytest.param(
        "seed() => 7\nexport type T\n    int total = seed()\n",
        id="user_function",
    ),
    pytest.param(
        "int seed = 7\nexport type T\n    int total = seed\n",
        id="global_variable",
    ),
]
_HISTORY_BODY = (
    "previous() => session.ispostmarket[1]\n"
    "export type T\n    bool value = previous()\n"
)


def _lib(version: int, body: str) -> dict[str, str]:
    return {LIBRARY: f'//@version={version}\nlibrary("Defaults")\n' + body}


def _strategy(version: int = 6) -> str:
    return (
        f'//@version={version}\nstrategy("Field defaults")\n'
        f"import {LIBRARY} as L\nbook = L.T.new()\n"
    )


def _refusal(source, code, *, libraries=None):
    with pytest.raises(CompileError) as caught:
        transpile(source, libraries=libraries)
    assert [d.code for d in caught.value.diagnostics] == [code]
    return caught.value.diagnostics[0]


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip("The C++ reference commit is unavailable")
    return tree


def _assert_unchanged(source, reference, tmp_path, *, libraries=None):
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
    before = transpile_json(pine, root=reference, extra_env=extra_env)
    if before["ok"]:
        assert transpile(source, libraries=libraries) == before["cpp"]
    else:
        with pytest.raises(CompileError) as caught:
            transpile(source, libraries=libraries)
        codes = [d.code for d in caught.value.diagnostics]
        assert "PF-E2087" not in codes
        assert codes == [d["code"] for d in before["diagnostics"]]
    return before


@pytest.mark.parametrize("source", _V5_CASES)
def test_v5_main_defaults_keep_version_refusal(source):
    # Main scripts require v6 before the analyzer visits any type defaults.
    _refusal(source, "PF-E2002")


@pytest.mark.parametrize("source", _V5_CASES)
def test_v5_main_defaults_match_reference(source, reference, tmp_path):
    _assert_unchanged(source, reference, tmp_path)


@pytest.mark.parametrize("body", _LIBRARY_CASES)
def test_v5_library_defaults_stay_accepted(body):
    assert transpile(_strategy(), libraries=_lib(5, body))


@pytest.mark.parametrize("body", _LIBRARY_CASES)
def test_v5_library_defaults_match_reference(body, reference, tmp_path):
    before = _assert_unchanged(
        _strategy(), reference, tmp_path, libraries=_lib(5, body),
    )
    assert before["ok"], before


@pytest.mark.parametrize("body", _LIBRARY_CASES)
def test_v6_library_defaults_are_refused_in_the_library(body):
    diagnostic = _refusal(_strategy(), "PF-E2087", libraries=_lib(6, body))
    assert diagnostic.location.file == LIBRARY


def test_main_default_refusal_differs_only_by_version():
    source = _CASES[0].values[0]
    _refusal(source, "PF-E2087")
    _refusal(source.replace("//@version=6", "//@version=5", 1), "PF-E2002")


def test_v5_main_importing_v6_default_keeps_version_refusal():
    _refusal(
        _strategy(5), "PF-E2002",
        libraries=_lib(6, _LIBRARY_CASES[0].values[0]),
    )


def test_v5_library_default_keeps_session_history_reason():
    source = _strategy() + 'if book.value\n    strategy.entry("L", strategy.long)\n'
    diagnostic = _refusal(
        source, "PF-E5048", libraries=_lib(5, _HISTORY_BODY),
    )
    reason = "which a UDT field default calls once for every new()"
    assert diagnostic.args["why"] == reason
    assert reason in diagnostic.message
    assert diagnostic.location.file == LIBRARY
    diagnostic = _refusal(
        source, "PF-E2087", libraries=_lib(6, _HISTORY_BODY),
    )
    assert diagnostic.location.file == LIBRARY


def test_v5_library_history_default_matches_reference(reference, tmp_path):
    source = _strategy() + 'if book.value\n    strategy.entry("L", strategy.long)\n'
    before = _assert_unchanged(
        source, reference, tmp_path, libraries=_lib(5, _HISTORY_BODY),
    )
    assert not before["ok"], before
    assert [d["code"] for d in before["diagnostics"]] == ["PF-E5048"]
