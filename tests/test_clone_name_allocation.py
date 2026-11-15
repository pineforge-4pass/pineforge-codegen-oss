"""Generated state and methods share a namespace with authored identifiers."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp
from tests._e2e import reference_codegen, transpile_json


_VAR_CLONE = """//@version=6
strategy("clone member spelling")
f() =>
    var float acc = 0.0
    acc += ta.ema(close, 3)
    acc
a = f()
b = f()
var float acc_cs1 = 0.0
acc_cs1 := acc_cs1 + 1.0
plot(a + b + acc_cs1)
"""

_METHOD_CLONE = """//@version=6
strategy("clone method spelling")
f(src) => ta.ema(src, 3)
a = f(close)
b = f(open)
var float f_cs1 = 2.0
plot(a + b + f_cs1)
"""


def _emit(source: str) -> tuple[CodeGen, str]:
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    generator = CodeGen(Analyzer(program).analyze())
    return generator, generator.generate()


def _scalar_member(cpp: str, name: str) -> None:
    declarations = re.findall(
        rf"^    double {re.escape(name)}(?: = [^;\n]+)?;$", cpp, re.M
    )
    assert len(declarations) == 1, name


def _method_body(cpp: str, name: str) -> str:
    declarations = list(re.finditer(
        rf"^    double {re.escape(name)}\([^\n]*\) \{{\n", cpp, re.M
    ))
    assert len(declarations) == 1, name
    start = declarations[0].end()
    return cpp[start:cpp.index("\n    }", start)]


def _has_identifier(cpp: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", cpp) is not None


@pytest.mark.parametrize("spelling", ["acc_cs1", "aa"])
def test_persistent_clone_reproducer_and_rename_control(spelling: str) -> None:
    generator, cpp = _emit(_VAR_CLONE.replace("acc_cs1", spelling))
    clone = generator._func_cs_var_remap[("f", 1)]["acc"]

    assert generator._safe_name(spelling) == spelling
    _scalar_member(cpp, spelling)
    _scalar_member(cpp, clone)
    assert clone == ("acc_cs1__pf2" if spelling == "acc_cs1" else "acc_cs1")
    body = _method_body(cpp, "f_cs1")
    assert _has_identifier(body, clone)
    assert not _has_identifier(body, spelling)
    assert re.search(rf"return {re.escape(clone)};", body)
    compile_cpp(cpp, label=f"persistent-clone-{spelling}")


@pytest.mark.parametrize("spelling", ["f_cs1", "aa"])
def test_callable_clone_reproducer_and_rename_control(spelling: str) -> None:
    generator, cpp = _emit(_METHOD_CLONE.replace("f_cs1", spelling))
    first = generator._func_variant_name("f", 0)
    second = generator._func_variant_name("f", 1)

    assert generator._safe_name(spelling) == spelling
    _scalar_member(cpp, spelling)
    assert first == "f_cs0"
    assert second == ("f_cs1__pf2" if spelling == "f_cs1" else "f_cs1")
    for output, method in (("a", first), ("b", second)):
        _method_body(cpp, method)
        assert re.search(rf"\b{output} = {re.escape(method)}\(", cpp)
    compile_cpp(cpp, label=f"callable-clone-{spelling}")


def test_clone_fallback_also_avoids_authored_fallback_spelling() -> None:
    source = (
        _VAR_CLONE
        + "var float acc_cs1__pf2 = 7.0\n"
        + "plot(acc_cs1__pf2)\n"
    )
    generator, cpp = _emit(source)
    clone = generator._func_cs_var_remap[("f", 1)]["acc"]

    assert clone == "acc_cs1__pf3"
    for name in ("acc_cs1", "acc_cs1__pf2", clone):
        _scalar_member(cpp, name)
    body = _method_body(cpp, "f_cs1")
    assert _has_identifier(body, clone)
    assert not _has_identifier(body, "acc_cs1__pf2")
    compile_cpp(cpp, label="occupied-clone-fallback")


def test_clone_member_cannot_be_hidden_by_an_authored_parameter() -> None:
    generator, cpp = _emit("""//@version=6
strategy("clone parameter spelling")
f(float acc_cs1) =>
    var float acc = 0.0
    acc += acc_cs1
    acc
a = f(close)
b = f(open)
plot(a + b)
""")
    clone = generator._func_cs_var_remap[("f", 1)]["acc"]
    assert clone == "acc_cs1__pf2"
    _scalar_member(cpp, clone)
    body = _method_body(cpp, "f_cs1")
    assert _has_identifier(body, "acc_cs1")
    assert _has_identifier(body, clone)
    assert re.search(rf"return {re.escape(clone)};", body)
    compile_cpp(cpp, label="clone-versus-parameter")


@pytest.mark.parametrize("storage", ["var ", ""], ids=["persistent", "ordinary"])
def test_function_member_prefix_is_allocated_against_authored_members(
    storage: str,
) -> None:
    source = """//@version=6
strategy("callable prefix spelling")
echo(float src) => src + 1.0
var float echo = 10.0
var float _fn_echo = 20.0
a = echo(close)
plot(a + echo + _fn_echo)
"""
    generator, cpp = _emit(source.replace("var float ", f"{storage}float "))
    method = generator._func_cpp_base_name("echo")

    assert method == "_fn_echo__pf2"
    _method_body(cpp, method)
    assert re.search(rf"\ba = {re.escape(method)}\(", cpp)
    for name in ("echo", "_fn_echo"):
        assert generator._safe_name(name) == name
        _scalar_member(cpp, name)
    compile_cpp(cpp, label="occupied-function-prefix")


def test_generated_method_base_cannot_alias_generated_storage_clone() -> None:
    generator, cpp = _emit("""//@version=6
strategy("generated method and storage spelling")
type Holder
    float value
method sample_cs1(Holder self) => self.value
leaf() =>
    var float _udt_Holder_sample = 0.0
    _udt_Holder_sample += close
    _udt_Holder_sample
a = leaf()
b = leaf()
var Holder holder = Holder.new(3.0)
c = holder.sample_cs1()
plot(a + b + c)
""")
    storage = generator._func_cs_var_remap[("leaf", 1)]["_udt_Holder_sample"]
    info = next(fi for fi in generator.ctx.func_infos if fi.is_udt_method)
    method = generator._func_cpp_base_name(info.name)

    assert storage == "_udt_Holder_sample_cs1"
    assert method == "_udt_Holder_sample_cs1__pf2"
    _scalar_member(cpp, storage)
    _method_body(cpp, method)
    assert re.search(rf"\bc = {re.escape(method)}\(", cpp)
    compile_cpp(cpp, label="generated-method-versus-storage")


def test_nested_instances_reserve_authored_method_and_state_spellings() -> None:
    generator, cpp = _emit("""//@version=6
strategy("nested instance spelling")
leaf(int size, float src) =>
    var float state = 0.0
    state += ta.highest(src, size)
    state
f_get(int len, float src) => leaf(len, src)
g_get(int len, float src) => leaf(len, src)
a = f_get(10, close)
b = f_get(20, open)
c = g_get(30, high)
var float leaf__ni1 = 2.0
var float state__ni1 = 3.0
plot(a + b + c + leaf__ni1 + state__ni1)
""")
    authored = {"leaf__ni1", "state__ni1"}
    assert generator._fresh_instances
    generated: list[str] = []
    for instance in generator._fresh_instances:
        method = instance["name"]
        generated.append(method)
        body = _method_body(cpp, method)
        for storage in instance["var_remap"].values():
            generated.append(storage)
            _scalar_member(cpp, storage)
            assert _has_identifier(body, storage)
        assert all(not _has_identifier(body, name) for name in authored)
    assert authored.isdisjoint(generated)
    assert len(generated) == len(set(generated))
    assert "leaf__ni1__pf2" in generated
    assert "state__ni1__pf2" in generated
    for name in authored:
        _scalar_member(cpp, name)
    compile_cpp(cpp, label="occupied-nested-instances")


def test_renamed_callable_context_keeps_compound_argument_history() -> None:
    generator, cpp = _emit("""//@version=6
strategy("clone context history spelling")
inner(float src) => src[1]
wrapped(float src) => inner(src + 1.0)
a = wrapped(close)
b = wrapped(open)
var float wrapped_cs1 = 2.0
plot(a + b + wrapped_cs1)
""")
    contexts = generator._inline_history_contexts_for_owner("wrapped")
    assert contexts == ["wrapped_cs0", "wrapped_cs1__pf2"]
    bridges = [
        info for info in generator._inline_history_members
        if info["kind"] in {"series_arg", "udf_series_arg"}
    ]
    assert set(contexts) <= {info["context"] for info in bridges}
    for index, context in enumerate(contexts):
        assert generator._func_variant_callsite[("wrapped", context)] == index
        body = _method_body(cpp, context)
        owned = [info for info in bridges if info["context"] == context]
        assert all(_has_identifier(body, info["member_name"]) for info in owned)
    compile_cpp(cpp, label="renamed-callable-history-context")


def test_typed_method_variants_reserve_authored_clone_spelling() -> None:
    generator, cpp = _emit("""//@version=6
strategy("typed method clone spelling")
type Holder
    float seed
method sample(Holder self, float src) => self.seed + src[1]
var Holder holder = Holder.new(3.0)
a = holder.sample(close + 1.0)
b = holder.sample(open + 1.0)
var float _udt_Holder_sample_cs1 = 2.0
plot(a + b + _udt_Holder_sample_cs1)
""")
    info = next(fi for fi in generator.ctx.func_infos if fi.is_udt_method)
    first = generator._func_variant_name(info.name, 0)
    second = generator._func_variant_name(info.name, 1)

    assert first == "_udt_Holder_sample_cs0"
    assert second == "_udt_Holder_sample_cs1__pf2"
    _scalar_member(cpp, "_udt_Holder_sample_cs1")
    for output, method in (("a", first), ("b", second)):
        _method_body(cpp, method)
        assert re.search(rf"\b{output} = {re.escape(method)}\(", cpp)
    assert any(info["kind"] == "series_arg"
               for info in generator._inline_history_members)
    compile_cpp(cpp, label="occupied-typed-method-clone")


@pytest.fixture(scope="module")
def clone_reference():
    reference = reference_codegen("4e51f6d00e3a015194d93b06e51ad90f2404cc14")
    if reference is None:
        pytest.skip("The clone spelling reference commit is unavailable")
    return reference


@pytest.mark.parametrize("source, original", [
    (_VAR_CLONE, "acc_cs1"),
    (_METHOD_CLONE, "f_cs1"),
])
def test_noncolliding_controls_keep_complete_reference_cpp(
    source, original, clone_reference, tmp_path
) -> None:
    source = source.replace(original, "aa")
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    reference = transpile_json(pine, root=clone_reference)
    assert reference["ok"], reference
    assert transpile(source) == reference["cpp"]
