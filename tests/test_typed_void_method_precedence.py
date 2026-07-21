"""Typed user methods must precede builtin void-terminal classification."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


CASES = (
    (
        "drawing-direct",
        '''//@version=6
strategy("drawing direct precedence")
method set_text(label self) => 11
method delegate(label self) => self.set_text()
var label drawing_usage = label.new(bar_index, close, "usage")
''',
        "_udt_label_delegate",
        "return _udt_label_set_text(self);",
    ),
    (
        "array-direct",
        '''//@version=6
strategy("array direct precedence")
method push(array<int> self) => 12
method delegate(array<int> self) => self.push()
''',
        "_udt_array_int_delegate",
        "return _udt_array_int_push(self);",
    ),
    (
        "map-direct",
        '''//@version=6
strategy("map direct precedence")
method clear(map<string, int> self) => 13
method delegate(map<string, int> self) => self.clear()
''',
        "_udt_map_string_int_delegate",
        "return _udt_map_string_int_clear(self);",
    ),
    (
        "drawing-shadow",
        '''//@version=6
strategy("drawing namespace shadow precedence")
method set_text(int self, label other) => 21
method delegate(int label, label other) => label.set_text(other)
var label drawing_usage = label.new(bar_index, close, "usage")
''',
        "_udt_int_delegate",
        "return _udt_int_set_text(label, other);",
    ),
    (
        "array-shadow",
        '''//@version=6
strategy("array namespace shadow precedence")
method push(int self, array<int> other) => 22
method delegate(int array, array<int> other) => array.push(other)
''',
        "_udt_int_delegate",
        "return _udt_int_push(array, other);",
    ),
    (
        "map-shadow",
        '''//@version=6
strategy("map namespace shadow precedence")
method clear(int self, map<string, int> other) => 23
method delegate(int map, map<string, int> other) => map.clear(other)
''',
        "_udt_int_delegate",
        "return _udt_int_clear(map, other);",
    ),
)


def _body(cpp: str, function_name: str) -> str:
    return cpp.split(f" {function_name}(", 1)[1].split("\n    }", 1)[0]


@pytest.mark.parametrize(
    ("label", "source", "delegate", "expected_return"),
    CASES,
)
def test_typed_user_method_precedes_void_builtin_classification(
    label: str,
    source: str,
    delegate: str,
    expected_return: str,
) -> None:
    cpp = transpile(source)
    body = _body(cpp, delegate)

    assert expected_return in body, f"{label}\n{body}"
    assert "return 0;" not in body
    assert "return 0.0;" not in body


def test_all_typed_precedence_cases_native_compile() -> None:
    for label, source, _delegate, _expected_return in CASES:
        compile_cpp(transpile(source), label=f"typed-void-precedence-{label}")


def test_genuine_builtin_void_terminals_keep_default_returns() -> None:
    source = '''//@version=6
strategy("genuine builtin void terminals")
method mutate_label(label self, string text) => self.set_text(text)
method mutate_array(array<int> self) => self.push(1)
method mutate_map(map<string, int> self) => self.clear()
var label drawing_usage = label.new(bar_index, close, "usage")
'''

    cpp = transpile(source)
    for function_name in (
        "_udt_label_mutate_label",
        "_udt_array_int_mutate_array",
        "_udt_map_string_int_mutate_map",
    ):
        body = _body(cpp, function_name)
        return_lines = [
            line.strip()
            for line in body.splitlines()
            if line.startswith("        return ")
        ]
        assert return_lines == ["return 0.0;"]
    compile_cpp(cpp, label="typed-void-precedence-genuine-builtins")


def test_typed_user_method_precedence_inside_terminal_if() -> None:
    source = '''//@version=6
strategy("typed precedence terminal if")
method push(array<int> self) => 42
method choose(array<int> self, bool enabled) =>
    if enabled
        self.push()
    else
        7
'''

    cpp = transpile(source)
    body = _body(cpp, "_udt_array_int_choose")

    assert "_func_ret = _udt_array_int_push(self);" in body
    compile_cpp(cpp, label="typed-void-precedence-terminal-if")


def test_typed_user_method_precedence_inside_terminal_switch() -> None:
    source = '''//@version=6
strategy("typed precedence terminal switch")
method clear(int self, map<string, int> other) => 43
method choose(int map, map<string, int> other, int mode) =>
    switch mode
        1 => map.clear(other)
        => 8
'''

    cpp = transpile(source)
    body = _body(cpp, "_udt_int_choose")

    assert "_func_ret = _udt_int_clear(map, other);" in body
    compile_cpp(cpp, label="typed-void-precedence-terminal-switch")
