"""Compile the same callable-local sources used by the scope comparisons.

The string-with-history shapes do not compile for any name because of a
separate known std::to_string defect, so those rows pin only that renaming
preserves the compile outcome, and no row here checks a value.
"""

import re

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests.test_builtin_named_locals_scope import (
    _ADDITIONAL_LOCAL_SHAPES,
    _LOCAL_NAMES,
    _PRIVATE_STORAGE_CASES,
    _additional_callable_local_source,
    _chart_local_source,
    _extended_callable_local_source,
    _program,
    _variant_source,
)


@pytest.mark.parametrize("name", ["aa", *_LOCAL_NAMES])
@pytest.mark.parametrize("shape", ["tuple", "typed", "chart"])
def test_all_builtin_named_numeric_families_compile(name, shape):
    compile_env.skip_if_no_compile_env()
    source = (
        _chart_local_source(name, string=False)
        if shape == "chart" else _variant_source(name, shape, string=False)
    )
    compile_env.compile_cpp(transpile(source), label=f"{shape}-{name}")


_REMAINING_SHAPES = [
    pytest.param("variant", shape, id=f"numeric-{shape}")
    for shape in ("history-free", "reassigned", "nested", "two-sites", "loop")
] + [
    pytest.param("extended", shape, id=shape)
    for shape in ("method", "varip", "for-in", "for-in-index", "for-in-value")
] + [
    pytest.param("additional", shape, id=shape) for shape in _ADDITIONAL_LOCAL_SHAPES
] + [
    pytest.param("string", "history-free", id="string-history-free"),
]


@pytest.mark.parametrize("name", ["aa", "second"])
@pytest.mark.parametrize("family,shape", _REMAINING_SHAPES)
def test_remaining_callable_local_shapes_compile(name, family, shape):
    compile_env.skip_if_no_compile_env()
    if family == "variant":
        source = _variant_source(name, shape, string=False)
    elif family == "extended":
        source = _extended_callable_local_source(name, shape)
    elif family == "additional":
        source = _additional_callable_local_source(name, shape)
    else:
        source = _variant_source(name, shape, string=True)
    compile_env.compile_cpp(transpile(source), label=f"{family}-{shape}-{name}")


@pytest.mark.parametrize("source", [
    pytest.param(case[1], id=case[0]) for case in _PRIVATE_STORAGE_CASES
])
def test_authored_private_storage_collisions_compile(source):
    compile_env.skip_if_no_compile_env()
    compile_env.compile_cpp(transpile(source), label="authored storage collision")


def _string_history_source(name, shape):
    if shape == "chart":
        return _chart_local_source(name, string=True)
    return _variant_source(name, shape, string=True)


def _compile_outcome(cpp, label):
    try:
        compile_env.compile_cpp(cpp, label=label)
    except AssertionError as error:
        parts = str(error).split("--- compiler diagnostics ---\n", 1)
        assert len(parts) == 2, str(error)
        diagnostics = parts[1].split("--- emitted C++", 1)[0]
        first_error = re.search(
            r"^.*?:[0-9]+(?::[0-9]+)?:\s*((?:fatal )?error:.*)$",
            diagnostics,
            re.MULTILINE,
        )
        assert first_error is not None, diagnostics
        return False, first_error[1]
    return True, None


def _normalize_compiler_error(message, name):
    spelling = re.escape(name)
    return re.sub(
        rf"\b(?:pf_safe_{spelling}|_{spelling}_|{spelling})"
        rf"(?:__pfhist[0-9]+)?(?:_cs[0-9]+|__ni[0-9]+)?\b",
        "aa",
        message,
    )


@pytest.mark.parametrize("shape", ["typed", "reassigned", "nested", "two-sites", "chart"])
def test_string_history_compile_outcome_matches_neutral_control(shape):
    compile_env.skip_if_no_compile_env()
    control_ok, control_error = _compile_outcome(
        transpile(_string_history_source("aa", shape)), f"string-{shape}-aa",
    )
    for name in ("second", "close", "time"):
        subject_ok, subject_error = _compile_outcome(
            transpile(_string_history_source(name, shape)), f"string-{shape}-{name}",
        )
        assert subject_ok == control_ok, (shape, name, subject_error, control_error)
        if not control_ok:
            assert _normalize_compiler_error(subject_error, name) == control_error, (
                shape, name, subject_error, control_error,
            )


# A string variable without a history read compiles for every name of the census.
@pytest.mark.parametrize("name", ["aa", *_LOCAL_NAMES])
def test_string_history_free_variable_compiles_for_every_name(name):
    compile_env.skip_if_no_compile_env()
    source = _variant_source(name, "history-free", string=True)
    compile_env.compile_cpp(transpile(source), label=f"string-history-free-{name}")
