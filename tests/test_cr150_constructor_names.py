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
from pineforge_codegen.codegen.helpers import CPP_EMITTER_NAMES, CPP_STANDARD_MACROS
from pineforge_codegen.codegen.host_members import HOST_MEMBER_NAMES
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
    # Check spelling before compile_cpp can skip for missing native inputs.
    assert f"struct pf_safe_{name} {{" in cpp
    compile_cpp(cpp + _REQUIRE_REPORT_HEADERS, label=f"CR-150 type {name}")


def test_trade_history_policy_macro_variable_transpiles_and_compiles() -> None:
    source = _PRELUDE + """int PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1 = bar_index
if PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1 > 0
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(source)
    assert re.search(r"\bint pf_safe_PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1\b", cpp)
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
