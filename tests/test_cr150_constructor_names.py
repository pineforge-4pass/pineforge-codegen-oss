"""CR-150 constructor lookup and identifiers exposed by the 1.7.0 headers.

Compile the raw transpiler output with tests._compile, as the corpus sweep
does. The lab must also run these compile cases with CXX=clang++; a text
assertion alone does not validate the constructor against the paired host.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CPP_RESERVED
from tests._compile import compile_cpp


_PRELUDE = '//@version=6\nstrategy("CR-150 constructor names")\n'
_REPORT_NAMES = ("report", "report_sink", "report_sequence")
# Appended after the raw output: an older header must not silently compile
# away the policy call and give these regressions a false green result.
_REQUIRE_REPORT_HEADERS = """
#ifndef PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1
#error CR-150 regressions require the paired 1.7.0 trade-history policy headers
#endif
namespace pf_cr150_report = pineforge::report;
namespace pf_cr150_report_sink = pineforge::report_sink;
namespace pf_cr150_report_sequence = pineforge::report_sequence;
"""


@pytest.mark.parametrize("name", _REPORT_NAMES)
def test_report_namespace_type_transpiles_and_compiles(name: str) -> None:
    """A legal Pine type must coexist with the engine's imported namespace."""
    source = _PRELUDE + f"""type {name}
    float value
sample = {name}.new(close)
if sample.value > open
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(source)
    # Compile before any spelling assertion so RED records the real namespace
    # ambiguity, rather than only a missing escape in the emitted text.
    compile_cpp(cpp + _REQUIRE_REPORT_HEADERS, label=f"CR-150 type {name}")
    assert f"struct pf_safe_{name} {{" in cpp


@pytest.mark.parametrize(
    "declaration",
    ("", "set_trade_history_streamable = close\n"),
    ids=("plain", "script-hides-policy-setter"),
)
def test_trade_history_constructor_compiles(declaration: str) -> None:
    """Exercise the enabled policy call, including a colliding script member."""
    cpp = transpile(_PRELUDE + declaration)
    compile_cpp(cpp + _REQUIRE_REPORT_HEADERS, label="CR-150 policy constructor")


def test_constructor_qualifies_host_calls() -> None:
    """Paired with test_trade_history_constructor_compiles, including clang."""
    cpp = transpile(_PRELUDE)
    start = cpp.index("    explicit GeneratedStrategy()")
    end = cpp.index("    void set_strategy_override", start)
    constructor = cpp[start:end]
    assert (
        "#if defined(PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1)\n"
        "        this->set_trade_history_streamable(true);\n"
        "#endif"
    ) in constructor
    assert "        this->configure_pine_strategy(cfg);\n" in constructor


def test_reserved_names_remain_exported() -> None:
    """Use the existing codegen export, also consumed by _safe_name."""
    assert {*_REPORT_NAMES, "set_trade_history_streamable"} <= CPP_RESERVED


def test_noncolliding_type_keeps_its_name() -> None:
    cpp = transpile(_PRELUDE + "type TradeSummary\n    float value\n")
    assert "struct TradeSummary {" in cpp
    assert "struct pf_safe_TradeSummary {" not in cpp
