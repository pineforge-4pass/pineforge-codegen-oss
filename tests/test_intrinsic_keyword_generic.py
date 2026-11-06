"""Verified keyword names follow each callable's registry record."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import re

import pytest

from pineforge_codegen import signatures, transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level
from pineforge_codegen.signatures import IntrinsicFunc


def _source(expression):
    return (
        '//@version=6\nstrategy("pf_probe")\n'
        f"pf_value = {expression}\nplot(pf_value)\n"
    )


def _arguments(intrinsic):
    return [
        (param.name, f"{index + 2}.0")
        for index, param in enumerate(intrinsic.primary.params)
    ]


def _call(intrinsic, arguments):
    return f"{intrinsic.name}({', '.join(arguments)})"


def _expressions(cpp, intrinsic):
    short_name = intrinsic.name.rsplit(".", 1)[-1]
    expressions = []
    for match in re.finditer(rf"\bstd::{re.escape(short_name)}\(", cpp):
        depth = 1
        end = match.end()
        while depth and end < len(cpp):
            depth += (cpp[end] == "(") - (cpp[end] == ")")
            end += 1
        assert depth == 0
        expressions.append(cpp[match.start():end])
    assert expressions, intrinsic.name
    return expressions


@pytest.fixture(params=("sqrt", "atan2"))
def intrinsic_case(request, monkeypatch):
    """Synthetic parameters use existing dispatch without a visitor change."""
    original = signatures.MATH_FUNCTIONS[request.param]
    original_verified = original.keyword_names_verified
    existing_names = {
        param.name
        for registry in vars(signatures).values()
        if isinstance(registry, dict)
        for record in registry.values()
        if isinstance(record, IntrinsicFunc)
        for signature in record.signatures
        for param in signature.params
    }
    intrinsic = replace(original, keyword_names_verified=False, signatures=[
        replace(signature, params=[
            replace(param, name=f"pf_synthetic_argument_{index}")
            for index, param in enumerate(signature.params)
        ])
        for signature in original.signatures
    ])
    assert intrinsic.keyword_names_verified is False
    assert not existing_names.intersection(intrinsic.param_names)

    @contextmanager
    def with_verified_names():
        with monkeypatch.context() as patch:
            patch.setattr(intrinsic, "keyword_names_verified", True)
            yield
        assert intrinsic.keyword_names_verified is False

    with monkeypatch.context() as patch:
        patch.setitem(signatures.MATH_FUNCTIONS, request.param, intrinsic)
        yield intrinsic, with_verified_names
    assert signatures.MATH_FUNCTIONS[request.param] is original
    assert original.keyword_names_verified is original_verified


@pytest.mark.parametrize("context", (
    "{expression}",
    'request.security(syminfo.tickerid, "60", {expression})',
))
@pytest.mark.parametrize("check_support", (True, False))
def test_verified_names_accept_declared_keywords(
    intrinsic_case, context, check_support,
):
    intrinsic, with_verified_names = intrinsic_case
    arguments = _arguments(intrinsic)
    positional = _call(intrinsic, [value for _, value in arguments])
    expected = _expressions(transpile(
        _source(context.format(expression=positional)), check_support=check_support,
    ), intrinsic)
    named_forms = (
        [f"{name} = {value}" for name, value in arguments],
        [f"{name} = {value}" for name, value in reversed(arguments)],
        [arguments[0][1]]
        + [f"{name} = {value}" for name, value in arguments[1:]],
    )
    with with_verified_names():
        for named in named_forms:
            actual = transpile(
                _source(context.format(expression=_call(intrinsic, named))),
                check_support=check_support,
            )
            assert _expressions(actual, intrinsic) == expected


@pytest.mark.parametrize("context", (
    "{expression}",
    'request.security(syminfo.tickerid, "60", {expression})',
))
@pytest.mark.parametrize("check_support", (True, False))
@pytest.mark.parametrize("keyword_layout", (
    "{name} = 2.0",
    "\n    {name} = 1.0 + close",
))
def test_verified_names_refuse_an_unknown_keyword_at_its_computed_span(
    intrinsic_case, context, check_support, keyword_layout,
):
    intrinsic, with_verified_names = intrinsic_case
    arguments = _arguments(intrinsic)
    unknown = f"pf_unknown_{arguments[0][0]}"
    assert unknown not in intrinsic.param_names
    expression = _call(intrinsic, [
        *[value for _, value in arguments],
        keyword_layout.format(name=unknown),
    ])
    source = _source(context.format(expression=expression))
    start = source.index(f"{unknown} =")
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start)
    expected_args = {"function": intrinsic.name, "name": unknown,
                     "accepted": ", ".join(intrinsic.param_names)}
    expected_text = render_diagnostic("PF-E5090", expected_args)
    with with_verified_names():
        with pytest.raises(CompileError) as caught:
            transpile(
                source, filename="generic_keywords.pine",
                check_support=check_support,
            )
    diagnostics = [
        diagnostic for diagnostic in caught.value.diagnostics
        if diagnostic.level == Level.ERROR
    ]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "PF-E5090"
    assert diagnostic.args == expected_args
    assert (diagnostic.message, diagnostic.hint) == expected_text
    assert diagnostic.location.file == "generic_keywords.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (line, col, col + len(unknown))


@pytest.mark.parametrize("context", (
    "{expression}",
    'request.security(syminfo.tickerid, "60", {expression})',
))
@pytest.mark.parametrize("check_support", (True, False))
def test_clearing_verified_names_restores_identical_bytes(
    intrinsic_case, context, check_support,
):
    intrinsic, with_verified_names = intrinsic_case
    arguments = _arguments(intrinsic)
    unknown = f"pf_unknown_{arguments[0][0]}"
    positional = [value for _, value in arguments]
    forms = (
        positional,
        [f"{name} = {value}" for name, value in arguments],
        [*positional, f"{unknown} = 2.0"],
    )
    sources = [
        _source(context.format(expression=_call(intrinsic, form)))
        for form in forms
    ]
    expected = [
        transpile(source, check_support=check_support).encode("utf-8")
        for source in sources
    ]
    with with_verified_names():
        transpile(sources[0], check_support=check_support)
        transpile(sources[1], check_support=check_support)
        with pytest.raises(CompileError):
            transpile(sources[2], check_support=check_support)
    assert intrinsic.keyword_names_verified is False
    assert [
        transpile(source, check_support=check_support).encode("utf-8")
        for source in sources
    ] == expected


@pytest.mark.parametrize("verified_short_name", ("sqrt", "exp"))
@pytest.mark.parametrize("context", (
    "{expression}",
    'request.security(syminfo.tickerid, "60", {expression})',
))
@pytest.mark.parametrize("check_support", (True, False))
def test_shared_parameter_names_resolve_each_callable_record(
    monkeypatch, verified_short_name, context, check_support,
):
    originals = {
        short_name: signatures.MATH_FUNCTIONS[short_name]
        for short_name in ("sqrt", "exp")
    }
    shared_name = "pf_shared_argument"
    unknown = f"pf_unknown_{shared_name}"
    records = {
        short_name: replace(
            original,
            signatures=[replace(
                original.primary,
                params=[replace(original.primary.params[0], name=shared_name)],
            )],
            keyword_names_verified=short_name == verified_short_name,
        )
        for short_name, original in originals.items()
    }
    with monkeypatch.context() as patch:
        for short_name, record in records.items():
            patch.setitem(signatures.MATH_FUNCTIONS, short_name, record)
        for short_name, record in records.items():
            positional = _source(context.format(expression=_call(record, ["2.0"])))
            expected = transpile(positional, check_support=check_support)
            named = _source(context.format(expression=_call(
                record, [f"{shared_name} = 2.0"],
            )))
            assert _expressions(transpile(
                named, check_support=check_support,
            ), record) == _expressions(expected, record)
            source = _source(context.format(expression=_call(
                record, ["2.0", f"{unknown} = 3.0"],
            )))
            if short_name == verified_short_name:
                with pytest.raises(CompileError) as caught:
                    transpile(source, check_support=check_support)
                diagnostic, = [
                    diagnostic for diagnostic in caught.value.diagnostics
                    if diagnostic.level == Level.ERROR
                ]
                expected_args = {"function": record.name, "name": unknown,
                                 "accepted": shared_name}
                assert diagnostic.code == "PF-E5090"
                assert diagnostic.args == expected_args
                assert (diagnostic.message, diagnostic.hint) == render_diagnostic(
                    "PF-E5090", expected_args,
                )
                start = source.index(f"{unknown} =")
                line = source.count("\n", 0, start) + 1
                col = start - source.rfind("\n", 0, start)
                assert (
                    diagnostic.location.line, diagnostic.location.col,
                    diagnostic.location.end_col,
                ) == (line, col, col + len(unknown))
            else:
                before = transpile(source, check_support=check_support).encode("utf-8")
                with monkeypatch.context() as verify:
                    verify.setattr(record, "keyword_names_verified", True)
                    with pytest.raises(CompileError):
                        transpile(source, check_support=check_support)
                assert record.keyword_names_verified is False
                assert transpile(
                    source, check_support=check_support,
                ).encode("utf-8") == before
    for short_name, original in originals.items():
        assert signatures.MATH_FUNCTIONS[short_name] is original
