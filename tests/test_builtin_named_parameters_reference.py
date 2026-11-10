"""Unrelated callable C++ stays byte-identical to the reference tree."""

from __future__ import annotations

import pytest

from pineforge_codegen.ast_nodes import FuncDef
from pineforge_codegen.library_modules import parse_library_module
from tests._e2e import reference_codegen, transpile_json
from tests._pine_libraries import Layout, library_text
from tests.test_builtin_named_locals_scope import (
    _builtin_history_source, _chart_local_source, _outer_binding_source,
)
from tests.test_function_overload_resolution import NAMED_DEFAULTS, PROBE
from tests.test_method_function_syntax_calls import SCALAR_SOURCE, TUPLE_SOURCE
from tests.test_udf_series_parameter_history import _SOURCE


CPP_REFERENCE = "a156a7d2785c8729e54da7eb01c5d41ddd03df14"


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    assert tree is not None, "The pinned C++ reference commit must be available"
    return tree


_SOURCES = [
    pytest.param(PROBE, (), id="scalar-overloads"),
    pytest.param(NAMED_DEFAULTS, (), id="named-default-overloads"),
    pytest.param(_SOURCE, (), id="ordinary-history-parameters"),
    pytest.param(
        TUPLE_SOURCE.format(receiver="self", call="holder.pair(value=x)"),
        (), id="tuple-method",
    ),
    pytest.param(
        SCALAR_SOURCE.format(receiver="self", call="holder.shifted(value=x)"),
        (), id="scalar-method",
    ),
    pytest.param(
        _builtin_history_source(), (), id="unshadowed-chart-history",
    ),
    pytest.param(
        _builtin_history_source(requested=True), (), id="requested-builtin-history",
    ),
    pytest.param(
        _outer_binding_source(parameter=False), (), id="requested-global-binding",
    ),
    pytest.param(
        _chart_local_source("close", string=False), (), id="builtin-named-local",
    ),
    pytest.param(
        '//@version=6\nstrategy("library function")\n'
        "import pftest/Base/1 as base\nv = base.twice(close)\n",
        ("pftest/Base/1",), id="imported-function",
    ),
    pytest.param(
        '//@version=6\nstrategy("library type")\n'
        "import pftest/Signals/1 as signals\n"
        "b = signals.Bar2.new(low, high)\nv = b.width()\n",
        ("pftest/Signals/1", "pftest/Base/1"), id="imported-method",
    ),
    pytest.param(
        '''//@version=6
strategy("requested UDF history scope")
history(float src) => src[1]
outer(float src) => history(src)
chart = outer(close)
requested = request.security(syminfo.tickerid, "60", outer(close))
requestedLower = request.security_lower_tf(
    syminfo.tickerid, "1", outer(close)
)
''',
        (), id="requested-history-parameters",
    ),
]


def _cpp_pair(source, paths, reference, tmp_path):
    layout = Layout(tmp_path)
    environment = layout.pin_all("callable-history", source, *paths)
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference, extra_env=environment)
    after = transpile_json(pine, extra_env=environment)
    assert before["ok"], before
    assert after["ok"], after
    return before["cpp"], after["cpp"]


@pytest.mark.parametrize("source,paths", _SOURCES)
def test_scripts_without_builtin_named_parameters_keep_complete_cpp(
    source, paths, reference, tmp_path,
):
    before, after = _cpp_pair(source, paths, reference, tmp_path)
    assert after == before


def test_library_span_keeps_its_existing_private_names(reference, tmp_path):
    module = parse_library_module("pftest/Signals/1", library_text("pftest/Signals/1"))
    (span,) = module.functions["span"]
    assert isinstance(span, FuncDef)
    assert span.params == ["high", "low"]
    source = (
        '//@version=6\nstrategy("library parameter interface")\n'
        "import pftest/Signals/1 as signals\n"
        "v = signals.span(low = high, high = low)\n"
    )
    before, after = _cpp_pair(
        source, ("pftest/Signals/1", "pftest/Base/1"), reference, tmp_path,
    )
    # The inliner qualifies these parameters before the history pass sees them.
    assert "Signals_v1__span__high" in after
    assert "Signals_v1__span__low" in after
    assert after == before
