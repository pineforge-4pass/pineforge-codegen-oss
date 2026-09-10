"""Frontend selection happens at construction; risk limits stay statements.

Only transpilation and C++ compile-only checks run here. Native lifecycle and
real C metadata forwarding are tested by the engine's literal C++ fixtures.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


_SOURCES = [
    '//@version=6\nstrategy("empty")\nplot(close)\n',
    '//@version=6\nstrategy("ta")\nx = ta.sma(close, 3)\nplot(x)\n',
    '''//@version=6
strategy("cap declaration")
limit = input.int(2)
if bar_index == 1
    strategy.risk.max_intraday_filled_orders(limit)
strategy.entry("E", strategy.long)
''',
    '''//@version=6
strategy("ordered cap declarations")
strategy.risk.max_intraday_filled_orders(3)
if bar_index == 1
    strategy.risk.max_intraday_filled_orders(4)
    strategy.risk.max_intraday_filled_orders(3)
''',
    '''//@version=6
strategy("native method name shadow")
enable_pine_intraday_cap = input.int(2)
strategy.risk.max_intraday_filled_orders(enable_pine_intraday_cap)
''',
]


@pytest.mark.parametrize("source", _SOURCES)
def test_constructor_explicitly_selects_compatibility_with_old_engine_bridge(source):
    cpp = transpile(source)
    constructor = cpp.split("explicit GeneratedStrategy()", 1)[1].split(
        "void set_strategy_override", 1
    )[0]
    assert cpp.count("enable_pine_intraday_cap();") == 1
    assert cpp.count("attach_pine_execution_adapter();") == 1
    assert (
        "#if defined(PINEFORGE_HAS_EXPLICIT_PINE_EXECUTION_ADAPTER_V1)\n"
        "        pineforge::BacktestEngine::attach_pine_execution_adapter();\n"
        "#elif defined(PINEFORGE_HAS_EXPLICIT_PINE_CAP_V1)\n"
        "        pineforge::BacktestEngine::enable_pine_intraday_cap();\n"
        "#endif"
    ) in constructor
    assert "max_intraday_filled_orders_ =" not in constructor
    compile_cpp(cpp, label="pine-cap-explicit-constructor")


def test_conditional_risk_limit_is_still_in_on_bar_after_constructor():
    cpp = transpile(_SOURCES[2])
    select = cpp.index("enable_pine_intraday_cap();")
    on_bar = cpp.index("void on_bar(")
    statement = cpp.index("max_intraday_filled_orders_ = (int)(limit);")
    assert select < on_bar < statement
    assert cpp.count("max_intraday_filled_orders_ =") == 1
    prefix = cpp[on_bar:statement]
    assert "if (" in prefix and "pine_bar_index()" in prefix


def test_multiple_risk_limits_keep_source_order_and_do_not_reset_attachment():
    cpp = transpile(_SOURCES[3])
    statements = [
        line.strip() for line in cpp.splitlines()
        if "max_intraday_filled_orders_ =" in line
    ]
    assert statements == [
        "max_intraday_filled_orders_ = (int)(3);",
        "max_intraday_filled_orders_ = (int)(4);",
        "max_intraday_filled_orders_ = (int)(3);",
    ]
    assert cpp.count("enable_pine_intraday_cap();") == 1
    assert cpp.count("attach_pine_execution_adapter();") == 1


def test_cap_constructor_fallback_compiles_when_capability_is_absent():
    # Also run this suite against actual older supported engine headers. This
    # local branch control proves a generated source does not require a new
    # member when the capability is absent; it does not assert runtime parity.
    cpp = (
        "#include <pineforge/engine.hpp>\n"
        "#undef PINEFORGE_HAS_EXPLICIT_PINE_EXECUTION_ADAPTER_V1\n"
        "#undef PINEFORGE_HAS_EXPLICIT_PINE_CAP_V1\n"
        + transpile(_SOURCES[2])
    )
    compile_cpp(cpp, label="pine-cap-old-engine-constructor-bridge")


def test_cap_only_bridge_and_new_method_name_shadow():
    source = """//@version=6
strategy("execution method shadow")
attach_pine_execution_adapter = input.int(2)
strategy.risk.max_intraday_filled_orders(attach_pine_execution_adapter)
"""
    cpp = transpile(source)
    assert "pineforge::BacktestEngine::attach_pine_execution_adapter();" in cpp
    compile_cpp(cpp, label="pine-execution-method-shadow")
    # Prove the cap-only branch parses independently of the newer member.
    compile_cpp("#include <pineforge/engine.hpp>\n"
                "#undef PINEFORGE_HAS_EXPLICIT_PINE_EXECUTION_ADAPTER_V1\n" + cpp,
                label="pine-execution-cap-only-bridge")


def test_execution_attachment_is_before_metadata_and_not_in_script_reset():
    cpp = transpile(_SOURCES[0])
    constructor = cpp.split("explicit GeneratedStrategy()", 1)[1].split(
        "void set_strategy_override", 1)[0]
    assert "attach_pine_execution_adapter();" in constructor
    assert cpp.count("attach_pine_execution_adapter();") == 1
    assert "set_syminfo_metadata(" not in constructor
    # Runtime metadata is supplied by the host after strategy_create returns.
    assert "return new GeneratedStrategy();" in cpp
