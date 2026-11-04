"""Drawing collection aliases: which forms are accepted and which keep their refusal.

The semantics are those of the public issue #295 (the drawing collection aliases).
These checks read the translator's diagnostics and emitted code; they run no program.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import reference_codegen, transpile_json


KINDS = ("line", "box", "label", "linefill")
BASE = "1e2e72e37047ea22a4fdfc189584668e9567fdee"


def _source(body):
    return '//@version=6\nstrategy("Collection snapshots", overlay=true)\n' + body


def _new(kind, name, value):
    if kind == "linefill":
        return (
            f"{name}_a = line.new(0, {value}.0, 1, {value + 1}.0)\n"
            f"{name}_b = line.new(0, {value + 2}.0, 1, {value + 3}.0)\n"
            f"{name} = linefill.new({name}_a, {name}_b, color.red)\n"
        )
    ctor = {
        "line": f"line.new(0, {value}.0, 1, {value + 1}.0)",
        "box": f"box.new(0, {value}.0, 1, {value - 1}.0)",
        "label": f'label.new(0, {value}.0, "snapshot")',
    }[kind]
    return f"{name} = {ctor}\n"


def _get(kind, handle):
    if kind == "linefill":
        return f"line.get_y1(linefill.get_line1({handle}))"
    method = {"line": "get_y1", "box": "get_top", "label": "get_y"}[kind]
    return f"{kind}.{method}({handle})"


def _set(kind, handle, value):
    if kind == "linefill":
        return f"line.set_y1(linefill.get_line1({handle}), {value})"
    method = {"line": "set_y1", "box": "set_top", "label": "set_y"}[kind]
    return f"{kind}.{method}({handle}, {value})"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("use,line,column", [
    ("saved.clear()", 4, 1),
    ("array.pop(saved)", 4, 11),
    ("array.push(saved, saved.first())", 4, 12),
    ("saved := {kind}.all", 4, 1),
    ("f(array<{kind}> xs) => xs.size()\nn = f(saved)", 5, 7),
    ("previous = saved[1]", 4, 12),
    ("for [i, item] in saved\n    n = i", 4, 18),
    ("method size(array<{kind}> xs) => array.clear(xs)\nsaved.size()", 5, 1),
    ("array.sort(saved)", 4, 12),
    ("array.insert(saved, 0, saved.first())", 4, 14),
    ("array.concat(saved, array.new<{kind}>())", 4, 14),
    ("other = array.copy(saved)", 4, 20),
    ("[items, count] = [saved, 1]", 4, 19),
    ("type Holder\n    array<{kind}> items\nholder = Holder.new(saved)", 6, 21),
    ("type Holder\n    array<{kind}> items\nholder = Holder.new()\n"
     "holder.items := saved", 7, 17),
    ('requested = request.security(syminfo.tickerid, "60", saved)', 4, 54),
])
def test_unmeasured_collection_uses_keep_located_refusal(kind, check_support, use, line, column):
    source = _source(f"saved = {kind}.all\n" + use.format(kind=kind) + "\n")
    with pytest.raises(CompileError) as caught:
        transpile(source, check_support=check_support)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5086"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("body,line,column", [
    ("grab() =>\n    snap = {kind}.all\narr = grab()\narr.clear()\n", 4, 5),
    ("method grab(int self) =>\n    snap = {kind}.all\n", 4, 5),
    ("grab() =>\n    snap = {kind}.all\n    returned = snap\n", 5, 5),
    ("arr = if bar_index == 0\n    snap = {kind}.all\n"
     "else\n    other = {kind}.all\narr.clear()\n", 4, 5),
    ("arr = if bar_index == 0\n    0\nelse\n    snap = {kind}.all\n", 6, 5),
    ("arr = switch\n    bar_index == 0 =>\n        snap = {kind}.all\n", 5, 9),
    ("arr = switch bar_index\n    0 =>\n        0\n"
     "    =>\n        snap = {kind}.all\n", 7, 9),
    ("grab() =>\n    if bar_index == 0\n        snap = {kind}.all\n", 5, 9),
    ("arr = array.new<{kind}>()\narr := if bar_index == 0\n"
     "    snap = {kind}.all\n", 5, 5),
    ("grab() =>\n    for i = 0 to 1\n        snap = {kind}.all\n", 5, 9),
])
def test_declaration_tail_cannot_return_a_collection(kind, check_support, body, line, column):
    with pytest.raises(CompileError) as caught:
        transpile(_source(body.format(kind=kind)), check_support=check_support)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5086"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("body", [
    "saved = {kind}.all\ncount = saved.size()\n",
    "if bar_index == 0\n    saved = {kind}.all\n",
    "switch\n    bar_index == 0 =>\n        saved = {kind}.all\n",
    "for i = 0 to 1\n    saved = {kind}.all\n",
    "measure() =>\n    saved = {kind}.all\n    saved.size()\ncount = measure()\n",
    "count = if bar_index == 0\n    saved = {kind}.all\n    saved.size()\n"
    "else\n    0\n",
    "for item in {kind}.all\n    {kind}.delete(item)\n",
])
def test_nonpersistent_non_tail_aliases_and_iteration_remain_accepted(kind, check_support, body):
    # An unused block tail is not a return. Only the final declaration of a
    # block whose value is consumed must be refused.
    transpile(_source(body.format(kind=kind)), check_support=check_support)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("storage", ["var", "varip"])
@pytest.mark.parametrize("hint", ["", "array<{kind}> "])
@pytest.mark.parametrize("initializer", ["{kind}.all", "original"])
def test_persistent_alias_declaration_kind_is_refused(kind, check_support, storage, hint, initializer):
    # A persistent alias has no TradingView capture. This applies to any declaration name
    # and to both direct collection reads and aliases of non-var snapshots.
    source = _source(
        f"original = {kind}.all\n"
        + f"{storage} {hint.format(kind=kind)}archived = {initializer.format(kind=kind)}\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, check_support=check_support)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5086"
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 1)


@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("body,line,column", [
    ("saved = polyline.all\n", 3, 18),
    ("for item in polyline.all\n    n = 1\n", 3, 22),
])
def test_polyline_alias_and_iteration_require_an_engine_handle(check_support, body, line, column):
    with pytest.raises(CompileError) as caught:
        transpile(_source(body), check_support=check_support)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5085"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("declaration,column", [
    ("grab(arr = {kind}.all) => 1\n", 12),
    ("method grab(int self, arr = {kind}.all) => 1\n", 29),
])
def test_parameter_default_cannot_receive_a_collection(kind, check_support, declaration, column):
    with pytest.raises(CompileError) as caught:
        transpile(_source(declaration.format(kind=kind)), check_support=check_support)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5086"
    # MemberAccess locations point at "all", after the receiver and dot.
    assert (diagnostic.location.line, diagnostic.location.col) == (3, column + len(kind) + 1)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("check_support", [True, False])
def test_persistent_snapshot_at_its_declaration_stays_refused(kind, check_support):
    source = _source(
        _new(kind, "current", 11)
        + f"""var saved = {kind}.all
{_set(kind, 'saved.first()', '77.0')}
observed_current = {_get(kind, 'current')}
observed_oldest = {_get(kind, 'saved.first()')}
observed_saved = saved.size()
observed_live = {kind}.all.size()
"""
    )
    # This is the open TradingView question: a once-initialized alias may
    # remain live there. Do not encode an unmeasured snapshot answer.
    with pytest.raises(CompileError) as caught:
        transpile(source, check_support=check_support)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5086"
    assert (diagnostic.location.line, diagnostic.location.col) == (
        6 if kind == "linefill" else 4, 1,
    )


@pytest.mark.parametrize("body", [
    "observed = close + open\n",
    _new("line", "h", 11) + "values = array.from(h)\nh.set_y1(17.0)\n",
    "observed = line.all.size()\nline.delete(line.all.first())\n",
])
def test_unrelated_and_existing_direct_read_cpp_stays_byte_identical(body, tmp_path):
    source = _source(body)
    reference = reference_codegen(BASE)
    assert reference is not None, "Restore the base commit for byte comparisons."
    path = tmp_path / "strategy.pine"
    path.write_text(source, encoding="utf-8")
    before = transpile_json(path, root=reference)
    assert before["ok"], before["diagnostics"]
    assert transpile(source) == before["cpp"]
