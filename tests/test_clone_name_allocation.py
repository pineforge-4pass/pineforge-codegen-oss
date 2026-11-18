"""Generated state and methods share a namespace with authored identifiers."""

from __future__ import annotations

import os
import re
from unittest.mock import patch

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.helpers import is_emitter_temporary
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

_COLLECTION_CLONE = """//@version=6
strategy("clone versus collection history")
f() =>
    var float _pf_collection_hist = 0.0
    _pf_collection_hist += ta.ema(close, 3)
    _pf_collection_hist
a = f()
b = f()
var cs1 = array.new<float>(1, 0.0)
array.set(cs1, 0, close)
prev = cs1[1]
plot(a + b + (na(prev) ? 0.0 : array.get(prev, 0)))
"""

_SECURITY_CLONE = """//@version=6
strategy("clone versus security state")
f() =>
    var float _sec1 = 0.0
    _sec1 += ta.ema(close, 3)
    _sec1
a = f()
b = f()
c = f()
var float cs2 = 0.0
cs2 := cs2 + 1.0
h() => close + cs2
v0 = request.security(syminfo.tickerid, "60", close)
v1 = request.security(syminfo.tickerid, "60", h())
plot(a + b + c + v0 + v1)
"""

_BLOCK_MEMBER = """//@version=6
strategy("block member spelling")
var float x__blk1 = 100.0
float up = na
float down = na
if close > open
    var float x = 0.0
    x += 1.0
    up := x
if close <= open
    var float x = 0.0
    x += 2.0
    down := x
plot(x__blk1)
plot(nz(up) + nz(down))
"""

_RESERVED_CALLABLE = """//@version=6
strategy("reserved callable history")
inner(float src) => src[1]
_sec7(float src) => inner(src + 1.0)
a = _sec7(close)
b = _sec7(open)
plot(a + b)
"""

_FUNCTION_HISTORY_PREFIX = """//@version=6
strategy("callable prefix and global history")
global_hist_1(float src) => src + 1.0
var float global_hist_1 = 10.0
a = global_hist_1(close)
var float total = 0.0
total += close
observe() => total[1]
b = observe()
plot(a + global_hist_1 + b)
"""

_FRESH_FIXNAN = """//@version=6
strategy("nested fixnan storage")
leaf(float src) => fixnan(src)
left(float src) => leaf(src)
right(float src) => leaf(src)
a = left(bar_index % 3 == 0 ? na : close)
b = left(bar_index % 4 == 0 ? na : open)
c = right(bar_index % 5 == 0 ? na : high)
plot(a + b + c)
"""


def _emit(source: str) -> tuple[CodeGen, str]:
    # Observe the actual final generator without replacing any pipeline pass.
    # Public transpile still runs support checks, re-analysis and the allowlist.
    generated: list[CodeGen] = []
    generate = CodeGen.generate

    def record(generator: CodeGen) -> str:
        cpp = generate(generator)
        generated.append(generator)
        return cpp

    with patch.object(CodeGen, "generate", record):
        cpp = transpile(source, check_support=True, libraries={})
    return generated[-1], cpp


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


@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
def test_typed_method_variants_reserve_authored_clone_spelling(named: bool) -> None:
    source = """//@version=6
strategy("typed method clone spelling")
type Holder
    float seed
method sample(Holder self, float src) => self.seed + src[1]
var Holder holder = Holder.new(3.0)
a = holder.sample(close + 1.0)
b = holder.sample(open + 1.0)
var float _udt_Holder_sample_cs1 = 2.0
plot(a + b + _udt_Holder_sample_cs1)
"""
    if named:
        source = source.replace(
            "sample(close + 1.0)", "sample(src=close + 1.0)",
        ).replace("sample(open + 1.0)", "sample(src=open + 1.0)")
    generator, cpp = _emit(source)
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


@pytest.mark.parametrize("spelling", ["cs1", "aa"])
def test_clone_and_collection_history_keep_distinct_state(spelling: str) -> None:
    generator, cpp = _emit(re.sub(r"\bcs1\b", spelling, _COLLECTION_CLONE))
    clone = generator._func_cs_var_remap[("f", 1)]["_pf_collection_hist"]
    variable, = generator._collection_history_variables()
    history = generator._collection_history_member(variable)

    assert clone == "pf_safe__pf_collection_hist_cs1"
    assert history == f"_pf_collection_hist_{spelling}"
    assert clone != history
    _scalar_member(cpp, clone)
    body = _method_body(cpp, "f_cs1")
    assert _has_identifier(body, clone)
    assert not _has_identifier(body, history)
    assert f"return {clone};" in body
    assert f"{history}.open_slot({spelling}," in cpp
    assert re.search(rf"{re.escape(history)}\.(?:reference|value)\(1\)", cpp)
    assert f"{history}.close({spelling}" in cpp
    compile_cpp(cpp, label=f"clone-collection-history-{spelling}")


def test_reserved_authored_escape_and_composed_clone_do_not_alias() -> None:
    generator, cpp = _emit(
        _COLLECTION_CLONE
        + "var float _pf_collection_hist_cs1 = 7.0\n"
        + "plot(_pf_collection_hist_cs1)\n"
    )
    authored = generator._safe_name("_pf_collection_hist_cs1")
    clone = generator._func_cs_var_remap[("f", 1)]["_pf_collection_hist"]
    assert authored == "pf_safe__pf_collection_hist_cs1"
    assert clone == f"{authored}__pf2"
    for member in (authored, clone):
        _scalar_member(cpp, member)
    body = _method_body(cpp, "f_cs1")
    assert f"return {clone};" in body
    assert not _has_identifier(body, authored)
    compile_cpp(cpp, label="authored-escape-versus-composed-clone")


@pytest.mark.parametrize("spelling", ["cs2", "aa"])
def test_clone_and_requested_mutable_state_stay_distinct(spelling: str) -> None:
    generator, cpp = _emit(re.sub(r"\bcs2\b", spelling, _SECURITY_CLONE))
    clone = generator._func_cs_var_remap[("f", 2)]["_sec1"]
    state = generator._security_state_name(1, spelling)

    assert clone == "pf_safe__sec1_cs2"
    assert state == f"_sec1_{spelling}"
    assert clone != state
    assert _has_identifier(cpp, state)
    _scalar_member(cpp, clone)
    body = _method_body(cpp, "f_cs2")
    assert _has_identifier(body, clone)
    assert not _has_identifier(body, state)
    assert f"return {clone};" in body
    compile_cpp(cpp, label=f"clone-requested-state-{spelling}")


@pytest.mark.parametrize("spelling", ["x__blk1", "aa"])
def test_sibling_storage_avoids_an_authored_derived_name(spelling: str) -> None:
    generator, cpp = _emit(_BLOCK_MEMBER.replace("x__blk1", spelling))
    members = [
        meta[1] for meta in generator.ctx.var_member_metadata_by_node.values()
        if meta[0].name == "x"
    ]
    assert members == ["x", "x__blk2" if spelling == "x__blk1" else "x__blk1"]
    assert spelling not in members
    for member in [spelling, *members]:
        _scalar_member(cpp, member)
    assert f"up = {members[0]};" in cpp
    assert f"down = {members[1]};" in cpp
    assert f"{members[0]} += 1.0;" in cpp
    assert f"{members[1]} += 2.0;" in cpp
    compile_cpp(cpp, label=f"sibling-storage-{spelling}")


@pytest.mark.parametrize("stem", [
    "_pf_collection_hist", "_sec7", "_precalc", "_strat",
])
def test_composed_reserved_storage_avoids_a_parameter_at_its_escape(
    stem: str,
) -> None:
    parameter = f"pf_safe_{stem}_cs1"
    generator, cpp = _emit(f"""//@version=6
strategy("reserved clone parameter")
f(float {parameter}) =>
    var float {stem} = 0.0
    {stem} += {parameter}
    {stem}
a = f(close)
b = f(open)
plot(a + b)
""")
    clone = generator._func_cs_var_remap[("f", 1)][stem]
    assert is_emitter_temporary(f"{stem}_cs1")
    assert not is_emitter_temporary(clone)
    assert clone == f"{parameter}__pf2"
    _scalar_member(cpp, clone)
    body = _method_body(cpp, "f_cs1")
    assert _has_identifier(body, parameter)
    assert _has_identifier(body, clone)
    assert f"return {clone};" in body
    checkpoint = cpp.split("_pf_script_state_checkpoint_.emplace(_PFScriptState{", 1)[1]
    checkpoint = checkpoint.split("\n        });", 1)[0]
    assert re.search(rf"^\s+{re.escape(clone)},$", checkpoint, re.M)
    compile_cpp(cpp, label="reserved-clone-versus-escaped-parameter")


@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
def test_reserved_callable_variants_keep_their_history_contexts(named: bool) -> None:
    source = _RESERVED_CALLABLE
    if named:
        source = source.replace(
            "_sec7(close)", "_sec7(src=close)",
        ).replace("_sec7(open)", "_sec7(src=open)")
    generator, cpp = _emit(source)
    contexts = generator._inline_history_contexts_for_owner("_sec7")
    assert contexts == ["pf_safe__sec7_cs0", "pf_safe__sec7_cs1"]
    bridges = [
        info for info in generator._inline_history_members
        if info["kind"] in {"series_arg", "udf_series_arg"}
    ]
    for index, context in enumerate(contexts):
        assert generator._func_variant_callsite[("_sec7", context)] == index
        body = _method_body(cpp, context)
        owned = [info for info in bridges if info["context"] == context]
        assert owned
        assert all(_has_identifier(body, info["member_name"]) for info in owned)
        assert re.search(rf"\b{'ab'[index]} = {context}\(", cpp)
    compile_cpp(cpp, label="reserved-callable-history-contexts")


def test_callable_prefix_cannot_capture_fixed_global_history_storage() -> None:
    generator, cpp = _emit(_FUNCTION_HISTORY_PREFIX)
    method = generator._func_cpp_base_name("global_hist_1")
    assert method == "pf_safe__fn_global_hist_1"
    _method_body(cpp, method)
    assert re.search(rf"\ba = {method}\(", cpp)
    assert re.search(r"^    Series<double> _fn_global_hist_1;$", cpp, re.M)
    assert any(
        info["kind"] == "fn_global_hist" and info["member_name"] == "_fn_global_hist_1"
        for info in generator._inline_history_members
    )
    _scalar_member(cpp, "global_hist_1")
    compile_cpp(cpp, label="callable-prefix-versus-global-history")


def test_sibling_allocation_reserves_later_names_and_history_storage() -> None:
    source = _BLOCK_MEMBER.replace("up := x", "up := nz(x[1])").replace(
        "down := x", "down := nz(x[1])",
    ) + "var float x__blk2 = 7.0\nplot(x__blk2)\n"
    generator, cpp = _emit(source)
    members = [
        generator._safe_name(meta[1])
        for meta in generator.ctx.var_member_metadata_by_node.values()
        if meta[0].name == "x"
    ]
    assert members == ["x", "x__blk3"]
    for member in members:
        assert member in generator._series_var_member_names
        assert re.search(rf"^    Series<double> {member};$", cpp, re.M)
    for output, member in zip(("up", "down"), members):
        assignment = [
            line for line in cpp.splitlines() if re.match(rf"\s+{output} = ", line)
        ][-1]
        assert _has_identifier(assignment, member)
    _scalar_member(cpp, "x__blk1")
    _scalar_member(cpp, "x__blk2")
    compile_cpp(cpp, label="sibling-later-name-and-history")


def test_analyzer_only_storage_uses_collision_safe_cpp_escaping() -> None:
    source = re.sub(
        r"\bx\b", "_sec7",
        _BLOCK_MEMBER.replace("x__blk1", "pf_safe__sec7__blk1"),
    )
    generator, cpp = _emit(source)
    members = [
        meta[1] for meta in generator.ctx.var_member_metadata_by_node.values()
        if meta[0].name == "_sec7"
    ]
    assert members == ["_sec7", "_sec7__blk1"]
    safe = generator._safe_name(members[1])
    assert safe == "pf_safe__sec7__blk1_2"
    for member in ["pf_safe__sec7__blk1", members[0], safe]:
        _scalar_member(cpp, member)
    assert f"up = {members[0]};" in cpp
    assert f"down = {safe};" in cpp
    compile_cpp(cpp, label="analyzer-storage-escaping")


def test_callable_block_storage_keeps_parameters_and_clones_distinct() -> None:
    generator, cpp = _emit("""//@version=6
strategy("callable block member spelling")
f(float x__blk1) =>
    float result = x__blk1
    if close > open
        var float x = 0.0
        x += x__blk1
        result += x
    if close <= open
        var float x = 0.0
        x += x__blk1 * 2.0
        result += x
    result
a = f(close)
b = f(open)
plot(a + b)
""")
    assert generator.ctx.block_var_renames
    for index in (0, 1):
        remap = generator._func_cs_var_remap[("f", index)]
        members = [remap[name] for name in ("x", "x__blk2")]
        assert len(set(members)) == 2
        body = _method_body(cpp, f"f_cs{index}")
        assert "double x__blk1" in cpp
        assert _has_identifier(body, "x__blk1")
        for member in members:
            _scalar_member(cpp, member)
            assert _has_identifier(body, member)
    compile_cpp(cpp, label="callable-block-parameter-and-clones")


def test_fixed_fixnan_instances_retain_their_helper_namespace() -> None:
    generator, cpp = _emit(_FRESH_FIXNAN)
    fresh = generator._fresh_fixnan_members
    assert fresh
    for site, member in fresh:
        assert member.startswith(f"{site.member_name}__ni")
        assert not member.startswith("pf_safe_")
        _scalar_member(cpp, member)
        assert any(
            _has_identifier(_method_body(cpp, instance["name"]), member)
            for instance in generator._fresh_instances
        )
    compile_cpp(cpp, label="fixed-fixnan-instance-namespace")


@pytest.fixture(scope="module")
def clone_reference():
    reference = reference_codegen("4e51f6d00e3a015194d93b06e51ad90f2404cc14")
    if reference is None:
        unavailable = (pytest.fail if os.environ.get("PINEFORGE_REQUIRE_NATIVE") == "1"
                       else pytest.skip)
        unavailable("MISSING INPUT: the clone spelling reference commit is unavailable")
    return reference


@pytest.mark.parametrize("source, original", [
    (_VAR_CLONE, "acc_cs1"),
    (_METHOD_CLONE, "f_cs1"),
    (_BLOCK_MEMBER, "x__blk1"),
    (_RESERVED_CALLABLE, "_sec7"),
    (_FRESH_FIXNAN, None),
], ids=[f"{_VAR_CLONE}-acc_cs1", f"{_METHOD_CLONE}-f_cs1", None, None, None])
def test_noncolliding_controls_keep_complete_reference_cpp(
    source, original, clone_reference, tmp_path
) -> None:
    if original is not None:
        source = source.replace(original, "aa")
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    reference = transpile_json(pine, root=clone_reference)
    assert reference["ok"], reference
    assert transpile(source, check_support=True, libraries={}) == reference["cpp"]
