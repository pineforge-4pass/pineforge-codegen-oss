"""Raw emission contract for the first metrics-only certified subset."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile, transpile_full


HEADER = '//@version=6\nstrategy("metrics plain")\n'
POSITIVE = HEADER + 'strategy.close("L")\n'


def diagnostics(source: str) -> str:
    return "\n".join(item.message for item in transpile_full(source)["diagnostics"])


def test_close_only_stateless_source_emits_the_versioned_plain_contract():
    cpp = transpile(POSITIVE)

    assert "#include <pineforge/native_module.hpp>" in cpp
    assert (
        "inline constexpr ::pineforge::MetricsPlainDeclarationV1 "
        "_pf_metrics_plain_manifest_v1{"
    ) in cpp
    assert (
        "sizeof(::pineforge::MetricsPlainDeclarationV1), 1u, "
        "1u, 1u, 1u, 0u, 0u, 0u, 0u, 0u"
    ) in cpp
    assert "struct MetricsPlainHostV1<GeneratedStrategy>" in cpp
    assert (
        "void prepare_script_run(const Bar* bars, int n, "
        "bool allow_precalculation) final override"
    ) in cpp
    assert "void present_report(ReportC* out) const final override" in cpp
    assert "bool report_presenter_supports_non_full() const noexcept final override" in cpp
    assert re.search(
        r"std::int32_t strategy_metrics_only_capability_v1\(void\) noexcept",
        cpp,
    )
    assert "reinterpret_cast" not in cpp
    assert "static_cast<pf_bar_t*>(static_cast<void*>(bars))" in cpp
    assert "static_cast<pf_report_t*>(static_cast<void*>(out))" in cpp
    assert "native_module::run_backtest<GeneratedStrategy>" in cpp
    assert "native_module::run_backtest_full<GeneratedStrategy>" in cpp


def test_plain_contract_guard_is_outside_the_c_abi_block_and_ordered():
    cpp = transpile(POSITIVE)

    guard = cpp.index(
        "#ifdef PINEFORGE_HAS_PINE_METRICS_PLAIN_ADAPTER_V1"
    )
    manifest = cpp.index("namespace pineforge::native_module")
    abi = cpp.index('extern "C" {')
    assert guard < manifest < abi
    assert cpp.index("_pf_metrics_plain_manifest_v1", manifest) < abi
    assert cpp.index(
        "strategy_metrics_only_capability_v1(void)", abi
    ) > abi


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("value = ta.sma(close, 3)\n", "ta call sites"),
        ("value = close\n", "authored variables"),
        ("value = input.int(3)\n", "input calls"),
        (
            'value = request.security(syminfo.tickerid, "5", close)\n',
            "request calls",
        ),
        ("", "runtime calculation overrides"),
        ("", "pyramiding"),
        ("", "process_orders_on_close"),
        ("value = timenow\n", "timenow"),
    ],
)
def test_each_excluded_family_keeps_the_legacy_emitter_and_reports_reason(
    mutation: str, reason: str
):
    if reason == "runtime calculation overrides":
        source = '//@version=6\nstrategy("metrics plain", calc_on_every_tick=true)\n'
    elif reason == "pyramiding":
        source = '//@version=6\nstrategy("metrics plain", pyramiding=2)\n'
    elif reason == "process_orders_on_close":
        source = (
            '//@version=6\n'
            'strategy("metrics plain", process_orders_on_close=true)\n'
        )
    else:
        source = HEADER + mutation

    cpp = transpile(source)
    assert "strategy_metrics_only_capability_v1" not in cpp
    assert "_pf_metrics_plain_manifest_v1" not in cpp
    assert reason in diagnostics(source)


@pytest.mark.parametrize(
    "source",
    [
        HEADER + 'strategy("metrics plain", "short")\n',
        HEADER + 'strategy.entry("L", strategy.long, limit=close)\n',
        HEADER + 'strategy.entry("L", strategy.long, stop=close)\n',
        HEADER + "plot(close)\n",
    ],
)
def test_positional_priced_and_non_close_sources_are_not_certified(source):
    cpp = transpile(source)
    assert "_pf_metrics_plain_manifest_v1" not in cpp
    assert "strategy_metrics_only_capability_v1" not in cpp


def test_predicate_is_input_sensitive_across_close_spellings_and_order_families():
    close_all = transpile(HEADER + "strategy.close_all()\n")
    another_id = transpile(HEADER + 'strategy.close("other")\n')
    entry = transpile(HEADER + 'strategy.entry("L", strategy.long)\n')

    assert "_pf_metrics_plain_manifest_v1" in close_all
    assert "_pf_metrics_plain_manifest_v1" in another_id
    assert "_pf_metrics_plain_manifest_v1" not in entry


def test_outside_subset_is_byte_identical_when_the_new_gate_is_forced_false(
    monkeypatch,
):
    import pineforge_codegen.codegen.capabilities as capabilities

    source = HEADER + "plot(close)\n"
    natural = transpile(source)
    monkeypatch.setattr(
        capabilities,
        "metrics_plain_predicate",
        lambda _emitter: (False, ("forced test rejection",)),
    )
    forced_false = transpile(source)
    assert forced_false == natural


def test_positive_output_is_only_changed_by_the_positive_gate(monkeypatch):
    import pineforge_codegen.codegen.capabilities as capabilities

    certified = transpile(POSITIVE)
    monkeypatch.setattr(
        capabilities,
        "metrics_plain_predicate",
        lambda _emitter: (False, ("forced test rejection",)),
    )
    legacy = transpile(POSITIVE)
    assert certified != legacy
    assert "_pf_metrics_plain_manifest_v1" not in legacy


@pytest.mark.parametrize(
    "name",
    [
        "MetricsPlainDeclarationV1",
        "MetricsPlainHostV1",
        "native_module",
        "_pf_metrics_plain_manifest_v1",
        "strategy_metrics_only_capability_v1",
    ],
)
def test_metrics_helper_and_manifest_names_are_reserved(name: str):
    cpp = transpile(HEADER + f"{name} = 1\n")
    assert f"pf_safe_{name}" in cpp

