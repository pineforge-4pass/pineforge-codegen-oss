"""CR-150 constructor lookup and identifiers exposed by the 1.7.0 headers.

Compile the raw transpiler output with tests._compile, as the corpus sweep
does. The lab must also run these compile cases with CXX=clang++; a text
assertion alone does not validate the constructor against the paired host.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CPP_RESERVED
from pineforge_codegen.codegen.helpers import (
    CPP_EMITTER_NAMES, CPP_STANDARD_MACROS, cpp_code_only,
)
from pineforge_codegen.codegen.host_members import HOST_MEMBER_NAMES
from tests._compile import compile_cpp


_PRELUDE = '//@version=6\nstrategy("CR-150 constructor names")\n'
_REPORT_NAMES = ("report", "report_sink", "report_sequence")
_ENGINE_SCOPE_NAMES = (
    *_REPORT_NAMES, "source", "native_calendar", "RunFailureCode",
    "pine_color", "QtyType", "CommissionType", "XLoc", "YLoc",
    "RunFailureInfo", "normalize_timezone_for_posix",
)
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


@pytest.mark.parametrize("name", (*_ENGINE_SCOPE_NAMES, "set_trade_history_streamable"))
def test_report_namespace_type_transpiles_and_compiles(name: str) -> None:
    """A legal Pine type must coexist with imported and inherited engine names."""
    source = _PRELUDE + f"""type {name}
    float value
sample = {name}.new(close)
if sample.value > open
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(source)
    # Check spelling before compile_cpp can skip for missing native inputs.
    assert f"struct pf_safe_{name} {{" in cpp
    assert f"struct {name} {{" not in cpp
    compile_cpp(cpp + _REQUIRE_REPORT_HEADERS, label=f"CR-150 type {name}")


def test_source_variable_is_escaped_in_generated_code() -> None:
    """Pin raw variable/parameter escaping independently of legacy comparisons."""
    cpp = transpile(_PRELUDE + """read_source(float source) => source + 1.0
float source = close
source := source + 1.0
observed = read_source(source)
if observed > open
    strategy.entry("source", strategy.long)
""")
    code = cpp_code_only(cpp)
    assert re.search(r"\bpf_safe_source\b", code)
    assert re.search(r"^    double pf_safe_source\b", code, re.MULTILINE)
    assert "read_source(double pf_safe_source)" in code
    # For this fixture, base.py emits the host base; emit_top.py emits the
    # host include, constructor/adapter calls and override setter; and
    # checked_settings.py emits the config factory. No TA length adapter is
    # used. These are the only emitter-owned spellings containing `source`.
    for spelling in (
        "#include <pineforge/source/pine_strategy_host.hpp>",
        "pineforge::source::PineStrategyHost",
        "pineforge::source::PineStrategyConfig",
        "pineforge::source::StrategyOverrides",
    ):
        pattern = rf"(?<!\w){re.escape(spelling)}(?!\w)"
        assert re.search(pattern, code), spelling
        code = re.sub(pattern, " ", code)
    assert not re.search(r"\bsource\b", code)


def test_trade_history_policy_macro_variable_transpiles_and_compiles() -> None:
    source = _PRELUDE + """int PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1 = bar_index
if PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1 > 0
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(source)
    escaped = "pf_safe_PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1"
    assert escaped in cpp
    # The only place the raw macro name may still be spelled is the generated
    # `#if defined(...)` guard of the policy call; any other use would be the
    # script's variable reaching the preprocessor unescaped.
    rest = cpp.replace(escaped, "")
    guard = "#if defined(PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1)"
    assert rest.count("PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1") == rest.count(guard)
    compile_cpp(cpp + _REQUIRE_REPORT_HEADERS, label="CR-150 policy macro variable")


@pytest.mark.parametrize(
    ("declaration", "escaped_declaration"),
    (
        (
            """type strategy_reads_trade_history
    float value
sample = strategy_reads_trade_history.new(close)
if sample.value > open
    strategy.entry("L", strategy.long)
""",
            "struct pf_safe_strategy_reads_trade_history {",
        ),
        (
            """strategy_reads_trade_history(float value) => value
if strategy_reads_trade_history(close) > open
    strategy.entry("L", strategy.long)
""",
            "double pf_safe_strategy_reads_trade_history(",
        ),
    ),
    ids=("type", "function"),
)
def test_trade_history_export_name_transpiles_and_compiles(
    declaration: str, escaped_declaration: str,
) -> None:
    cpp = transpile(_PRELUDE + declaration)
    assert escaped_declaration in cpp
    export = "    int strategy_reads_trade_history(void) {"
    assert cpp.count(export) == 1
    assert export in cpp.split('extern "C" {', 1)[1]
    # Compile a call to the unescaped public export after the script's type
    # or function, so escaping the export itself cannot produce a false green.
    call_export = """
int pf_cr150_call_trade_history_export() {
    return ::strategy_reads_trade_history();
}
"""
    compile_cpp(
        cpp + _REQUIRE_REPORT_HEADERS + call_export,
        label="CR-150 trade-history export name",
    )


def _emitted_external_names() -> list[tuple[str, str]]:
    """Inventory emitted guards and C exports without invoking a compiler."""
    codegen = Path(__file__).resolve().parents[1] / "pineforge_codegen" / "codegen"
    guards: set[str] = set()
    exports: set[str] = set()
    declaration = re.compile(
        r"""["'] {4}(?!static\b)"""
        r"(?:[A-Za-z_]\w*(?:::\w+)*(?:[ \t]*[*&])?[ \t]+)+"
        r"([A-Za-z_][A-Za-z_0-9{}]*)[ \t]*\("
    )
    for path in sorted(codegen.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        directives = re.findall(
            r"#[ \t]*(?:if|ifdef|ifndef|elif)\b[^\r\n]*", source,
        )
        for directive in directives:
            guards.update(re.findall(r"\bPINEFORGE_HAS_[A-Z0-9_]+_V1\b", directive))
        # These emitters supply the extern "C" block and its inserted exports.
        # Select their bodies before scanning declarations, excluding class
        # methods and the block's internal static implementation helper.
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef) or not (
                node.name == "_emit_extern_c"
                or re.fullmatch(r"emit_\w+_exports", node.name)
            ):
                continue
            body = ast.get_source_segment(source, node)
            assert body is not None, f"cannot read export emitter {path.name}:{node.name}"
            templates = declaration.findall(body)
            assert templates, f"no C exports matched in {path.name}:{node.name}"
            # Checked setters interpolate a finite loop variable. Read its
            # choices from the emitter, rather than hand-maintaining exports.
            choices = {
                loop.target.id: [value.value for value in loop.iter.elts]
                for loop in ast.walk(node)
                if isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)
                and isinstance(loop.iter, (ast.Tuple, ast.List))
                and all(isinstance(value, ast.Constant) and isinstance(value.value, str)
                        for value in loop.iter.elts)
            }
            for template in templates:
                names = {template}
                for field in re.findall(r"\{(\w+)\}", template):
                    assert choices.get(field), (
                        f"unresolved export template {template} in {path.name}:{node.name}"
                    )
                    names = {
                        name.replace("{" + field + "}", value)
                        for name in names for value in choices[field]
                    }
                assert all("{" not in name and "}" not in name for name in names), (
                    f"unresolved export names in {path.name}:{node.name}: {names}"
                )
                exports.update(names)
    assert guards, "no PINEFORGE_HAS_*_V1 guards matched in codegen emitter sources"
    assert exports, 'no extern "C" exports matched in codegen emitter sources'
    return ([("guard", name) for name in sorted(guards)]
            + [("export", name) for name in sorted(exports)])


@pytest.mark.parametrize(
    ("kind", "name"), _emitted_external_names(), ids=lambda value: value,
)
def test_emitted_external_name_is_reserved(kind: str, name: str) -> None:
    reserved = CPP_STANDARD_MACROS | CPP_EMITTER_NAMES | HOST_MEMBER_NAMES
    assert name in reserved, f"unreserved emitted {kind}: {name}"


@pytest.mark.parametrize(
    "declaration",
    ("", "set_trade_history_streamable = close\n"),
    ids=("plain", "script-hides-policy-setter"),
)
def test_trade_history_constructor_compiles(declaration: str) -> None:
    """Exercise the enabled policy call, including a colliding script member."""
    cpp = transpile(_PRELUDE + declaration)
    compile_cpp(cpp + _REQUIRE_REPORT_HEADERS, label="CR-150 policy constructor")


def test_script_function_named_policy_setter_is_escaped() -> None:
    """A user function must leave the inherited policy setter reachable."""
    cpp = transpile(_PRELUDE + """set_trade_history_streamable(x) => x * 2
y = set_trade_history_streamable(close)
""")
    start = cpp.index("class GeneratedStrategy :")
    end = cpp.index('\nextern "C" {', start)
    strategy = cpp_code_only(cpp[start:end])
    assert "pf_safe_set_trade_history_streamable(" in strategy
    base_call = "this->set_trade_history_streamable(true);"
    assert strategy.count(base_call) == 1
    # The sole unescaped occurrence must be the inherited constructor call,
    # never a script member declaration or a call to the script function.
    assert not re.search(
        r"\bset_trade_history_streamable\b", strategy.replace(base_call, "", 1),
    )


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
    assert {*_ENGINE_SCOPE_NAMES, "set_trade_history_streamable"} <= CPP_RESERVED


def test_noncolliding_type_keeps_its_name() -> None:
    cpp = transpile(_PRELUDE + "type TradeSummary\n    float value\n")
    assert "struct TradeSummary {" in cpp
    assert "struct pf_safe_TradeSummary {" not in cpp
