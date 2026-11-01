"""Emitted helper calls must remain reachable beside authored functions."""

import ast
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import CPP_RESERVED, cpp_code_only
from pineforge_codegen.codegen.tables import BAR_BUILTINS
from tests._compile import compile_cpp


_PINE_CALL = re.compile(
    r"\b(pine_[A-Za-z0-9_]+)\s*(?:<[^;{}()\n]+>)?\s*\("
)
_PINE_NAME = re.compile(r"pine_[A-Za-z0-9_]+")


def _emitted_pine_calls() -> dict[str, set[str]]:
    """Read literal calls, dispatch values and finite callee templates."""
    codegen = Path(__file__).resolve().parents[1] / "pineforge_codegen" / "codegen"
    calls: dict[str, set[str]] = {}
    for path in sorted(codegen.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings: set[int] = set()
        dispatch_values: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                if node.body and isinstance(node.body[0], ast.Expr):
                    docstrings.add(id(node.body[0].value))
            if isinstance(node, ast.Dict):
                dispatch_values.update(id(value) for value in node.values)
            # A literal membership guard closes interpolated session callees.
            if (isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and len(node.test.ops) == 1
                    and isinstance(node.test.ops[0], ast.In)
                    and isinstance(node.test.comparators[0],
                                   (ast.Tuple, ast.List, ast.Set))):
                choices = node.test.comparators[0].elts
                if not all(isinstance(choice, ast.Constant)
                           and isinstance(choice.value, str) for choice in choices):
                    continue
                for template in ast.walk(node):
                    if (not isinstance(template, ast.JoinedStr)
                            or len(template.values) < 2):
                        continue
                    prefix, field = template.values[:2]
                    if (isinstance(prefix, ast.Constant)
                            and isinstance(prefix.value, str)
                            and re.fullmatch(r"pine_[A-Za-z0-9_]*", prefix.value)
                            and isinstance(field, ast.FormattedValue)
                            and ast.dump(field.value) == ast.dump(node.test.left)):
                        for choice in choices:
                            name = prefix.value + choice.value
                            calls.setdefault(name, set()).add(
                                f"{path.name}:{template.lineno}")
        for node in ast.walk(tree):
            if (not isinstance(node, ast.Constant)
                    or not isinstance(node.value, str)
                    or id(node) in docstrings):
                continue
            names = set(_PINE_CALL.findall(cpp_code_only(node.value)))
            # log.* selects a bare callee from a dictionary before adding '('.
            if id(node) in dispatch_values and _PINE_NAME.fullmatch(node.value):
                names.add(node.value)
            for name in names:
                calls.setdefault(name, set()).add(f"{path.name}:{node.lineno}")
    # BAR_BUILTINS expands its finite calendar-name comprehension on import.
    for expression in BAR_BUILTINS.values():
        for name in _PINE_CALL.findall(expression):
            calls.setdefault(name, set()).add("tables.py:BAR_BUILTINS")
    return calls


def test_every_emitted_pine_function_name_is_reserved() -> None:
    calls = _emitted_pine_calls()
    assert calls, "No emitted helper calls found in the codegen sources"
    missing = {name: locations for name, locations in sorted(calls.items())
               if name not in CPP_RESERVED}
    # Every extracted helper callee belongs to the allocator's reserved names.
    assert missing == {}


@pytest.mark.parametrize(
    ("function_name", "level", "definition", "user_call", "builtin_call"),
    (
        # The reserved info helper keeps its name; the user function gains pf_safe_.
        (
            "pine_log_info", "info",
            "pf_safe_pine_log_info(std::string text) {",
            'pf_safe_pine_log_info(std::string("user"))',
            'pine_log_info(std::string("abc"));',
        ),
        # The warning helper and the user's function follow the same allocation rule.
        (
            "pine_log_warning", "warning",
            "pf_safe_pine_log_warning(std::string text) {",
            'pf_safe_pine_log_warning(std::string("user"))',
            'pine_log_warning(std::string("abc"));',
        ),
        # The error helper and the user's function follow the same allocation rule.
        (
            "pine_log_error", "error",
            "pf_safe_pine_log_error(std::string text) {",
            'pf_safe_pine_log_error(std::string("user"))',
            'pine_log_error(std::string("abc"));',
        ),
    ),
)
def test_user_function_cannot_capture_builtin_log(
    function_name: str, level: str, definition: str, user_call: str,
    builtin_call: str,
) -> None:
    cpp = transpile(f"""//@version=6
strategy("Log function names")
{function_name}(string text) => str.length(text)
size = {function_name}("user")
if bar_index == 0
    log.{level}("abc")
""")
    # Match whole identifiers so an escaped user call cannot stand in for the builtin.
    for snippet in (definition, user_call, builtin_call):
        assert re.search(r"(?<![A-Za-z0-9_])" + re.escape(snippet), cpp), snippet
    compile_cpp(cpp, label=function_name)
