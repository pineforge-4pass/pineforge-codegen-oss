"""Pine v6 user-defined object identity, copy, and rollback regressions.

These probes cover UDTs whose fields are scalar, nested UDT, or shared-ID
matrix values.  Array-valued UDT fields intentionally remain outside this
claim; arrays *of* UDT handles are covered separately below.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase
from tests._compile import compile_cpp
from tests.test_pinemap_semantics import _compile_and_run


_ALIAS_COPY_SOURCE = r'''//@version=6
strategy("UDT object identity")
type Item
    float value
type Box
    Item inner
mutate_then_rebind(Item item, Item replacement) =>
    item.value += 1.0
    item := replacement
    item.value += 2.0
    item.value
var Item original = Item.new(10.0)
var Item alias = original
var Item detached = original.copy()
var Item functional = Item.copy(original)
var Box holder = Box.new(original)
alias.value += 5.0
detached.value := 99.0
functional.value := 77.0
paramResult = mutate_then_rebind(original, detached)
observedOriginal = original.value
observedAlias = alias.value
observedDetached = detached.value
observedFunctional = functional.value
observedNested = holder.inner.value
'''


def test_udt_values_emit_nullable_handles_records_and_arenas() -> None:
    cpp = transpile(_ALIAS_COPY_SOURCE)
    assert "struct Item {\n    int32_t __pf_id = -1;" in cpp
    assert "struct _PFUdtRecord_Item" in cpp
    assert (
        "_PFUdtArena<Item, _PFUdtRecord_Item> "
        "_pf_udt_Item{&_pf_udt_undo};"
    ) in cpp
    assert "alias = original;" in cpp
    assert "detached = _pf_udt_Item.copy(original);" in cpp
    assert "functional = _pf_udt_Item.copy(original);" in cpp
    assert "_pf_udt_Item.get(alias).value += 5.0;" in cpp
    assert "Item& alias" not in cpp
    assert "Item* alias" not in cpp
    compile_cpp(cpp, label="udt-object-handles")


def test_udt_alias_copy_and_parameter_rebind_runtime() -> None:
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.original.__pf_id != strategy.alias.__pf_id) return 3;
    if (strategy.original.__pf_id == strategy.detached.__pf_id) return 4;
    if (strategy.original.__pf_id == strategy.functional.__pf_id) return 5;
    if (strategy.observedOriginal != 16.0) return 6;
    if (strategy.observedAlias != 16.0) return 7;
    if (strategy.observedDetached != 101.0) return 8;
    if (strategy.observedFunctional != 77.0) return 9;
    if (strategy.observedNested != 16.0) return 10;
    if (strategy.paramResult != 101.0) return 11;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        transpile(_ALIAS_COPY_SOURCE) + driver,
        label="udt-alias-copy-runtime",
    ) == "ok\n"


_MATRIX_FINGERPRINT_SOURCE = r'''//@version=6
strategy("UDT matrix shallow copy")
type Holder
    float scalar
    matrix<int> nested
var matrix<int> shared = matrix.new<int>(1, 1, 7)
var Holder original = Holder.new(10.0, shared)
var Holder alias = original
var Holder copied = original.copy()
alias.scalar += 5.0
copied.scalar := 99.0
copied.nested.set(0, 0, 13)
observedOriginal = original.scalar
observedAlias = alias.scalar
observedCopy = copied.scalar
observedOriginalNested = original.nested.get(0, 0)
observedAliasNested = alias.nested.get(0, 0)
observedCopyNested = copied.nested.get(0, 0)
'''


def test_udt_outer_copy_detaches_but_nested_matrix_stays_shared_runtime() -> None:
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.observedOriginal << " "
              << strategy.observedAlias << " "
              << strategy.observedCopy << " "
              << strategy.observedOriginalNested << " "
              << strategy.observedAliasNested << " "
              << strategy.observedCopyNested << "\n";
}
'''
    assert _compile_and_run(
        transpile(_MATRIX_FINGERPRINT_SOURCE) + driver,
        label="udt-matrix-fingerprint-runtime",
    ) == "15 15 99 13 13 13\n"


_MAP_FIELD_COPY_SOURCE = r'''//@version=6
strategy("UDT map shallow copy")
type Holder
    map<string, int> data
var map<string, int> shared = map.new<string, int>()
var Holder original = Holder.new(shared)
var Holder copied = original.copy()
copied.data.put("shared", 7)
seenThroughOriginal = original.data.get("shared")
copied.data := map.new<string, int>()
copied.data.put("private", 9)
originalAfterRebind = original.data.get("shared")
copyPrivate = copied.data.get("private")
'''


def test_udt_outer_copy_shallow_shares_map_then_allows_field_rebind_runtime() -> None:
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.original.__pf_id == strategy.copied.__pf_id) return 3;
    if (strategy.seenThroughOriginal != 7) return 4;
    if (strategy.originalAfterRebind != 7) return 5;
    if (strategy.copyPrivate != 9) return 6;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        transpile(_MAP_FIELD_COPY_SOURCE) + driver,
        label="udt-map-field-copy-runtime",
    ) == "ok\n"


_COOF_CYCLE_SOURCE = r'''//@version=6
strategy("UDT cyclic checkpoint", calc_on_order_fills=true)
type Node
    Node next = na
    float value
var Node root = Node.new(na, 1.0)
var Node alias = root
var Node copied = root.copy()
if barstate.isfirst
    root.next := root
    copied.value := 3.0
'''


def test_udt_checkpoint_restores_arena_topology_cycle_safely_runtime() -> None:
    cpp = transpile(_COOF_CYCLE_SOURCE)
    assert "struct _PFCheckpointTraits<_PFUdtRecord_Node>" in cpp
    assert "struct _PFCheckpointTraits<_PFUdtArena<Node, _PFUdtRecord_Node>>" in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy._pf_udt_Node.size() != 2) return 3;
    const int32_t root_id = strategy.root.__pf_id;
    const int32_t copied_id = strategy.copied.__pf_id;
    strategy.snapshot_script_state();

    strategy._pf_udt_Node.get(strategy.alias).value = 9.0;
    strategy._pf_udt_Node.get(strategy.copied).next = strategy.copied;
    _PFUdtRecord_Node extra{};
    extra.value = 7.0;
    strategy.root = strategy._pf_udt_Node.create(extra);
    if (strategy._pf_udt_Node.size() != 3) return 4;

    strategy.restore_script_state();
    if (strategy._pf_udt_Node.size() != 2) return 5;
    if (strategy.root.__pf_id != root_id) return 6;
    if (strategy.alias.__pf_id != root_id) return 7;
    if (strategy.copied.__pf_id != copied_id) return 8;
    const auto& root_record = strategy._pf_udt_Node.get(strategy.root);
    const auto& copied_record = strategy._pf_udt_Node.get(strategy.copied);
    if (root_record.value != 1.0) return 9;
    if (root_record.next.__pf_id != root_id) return 10;
    if (copied_record.value != 3.0) return 11;
    if (!is_na(copied_record.next)) return 12;

    strategy._pf_udt_Node.get(strategy.alias).value = 11.0;
    strategy._pf_udt_Node.create(extra);
    strategy.restore_script_state();
    if (strategy._pf_udt_Node.size() != 2) return 13;
    if (strategy._pf_udt_Node.get(strategy.root).value != 1.0) return 14;

    strategy._pf_udt_Node.get(strategy.alias).value = 5.0;
    strategy.commit_script_state();
    strategy._pf_udt_Node.get(strategy.alias).value = 6.0;
    strategy._pf_udt_Node.create(extra);
    strategy.restore_script_state();
    if (strategy._pf_udt_Node.size() != 2) return 15;
    if (strategy._pf_udt_Node.get(strategy.root).value != 5.0) return 16;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-cycle-checkpoint-runtime",
    ) == "ok\n"


def test_udt_checkpoint_restores_shared_map_in_reverse_first_touch_order_runtime() -> None:
    source = r'''//@version=6
strategy("UDT ordered alias rollback", calc_on_order_fills=true)
type Holder
    map<string, int> data
var map<string, int> shared = map.new<string, int>()
var Holder first = Holder.new(shared)
var Holder second = first.copy()
'''
    cpp = transpile(source)
    assert "class _PFUdtUndoCoordinator" in cpp
    assert "std::vector<std::function<void()>> _pf_undo_;" in cpp
    assert "_pf_undo_.rbegin()" in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy._pf_udt_Holder.size() != 2) return 3;

    strategy.snapshot_script_state();
    strategy._pf_udt_Holder.get(strategy.first).data.put("value", 1);
    strategy._pf_udt_Holder.get(strategy.second).data.put("value", 2);
    strategy.restore_script_state();
    if (strategy._pf_udt_Holder.get(strategy.first).data.contains("value")) return 4;
    if (strategy._pf_udt_Holder.get(strategy.second).data.contains("value")) return 5;

    strategy.snapshot_script_state();
    const Holder speculative = strategy._pf_udt_Holder.copy(strategy.first);
    strategy._pf_udt_Holder.get(speculative).data.put("new-record", 7);
    if (!strategy._pf_udt_Holder.get(strategy.first).data.contains("new-record")) return 6;
    strategy.restore_script_state();
    if (strategy._pf_udt_Holder.size() != 2) return 7;
    if (strategy._pf_udt_Holder.get(strategy.first).data.contains("new-record")) return 8;
    if (strategy._pf_udt_Holder.get(strategy.second).data.contains("new-record")) return 9;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-ordered-alias-rollback",
    ) == "ok\n"


def test_cross_type_udt_shared_map_restores_in_global_touch_order_runtime() -> None:
    source = r'''//@version=6
strategy("cross-type UDT alias rollback", calc_on_order_fills=true)
type First
    map<string, int> data
type Second
    map<string, int> data
var First first = First.new(map.new<string, int>())
var Second second = Second.new(first.data)
'''
    cpp = transpile(source)
    coordinator_restore = (
        "_PFCheckpointTraits<decltype(GeneratedStrategy::_pf_udt_undo)>::restore"
    )
    first_arena_restore = (
        "_PFCheckpointTraits<decltype(GeneratedStrategy::_pf_udt_First)>::restore"
    )
    assert cpp.index(coordinator_restore) < cpp.index(first_arena_restore)
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;

    strategy.snapshot_script_state();
    strategy._pf_udt_First.get(strategy.first).data.put("value", 1);
    strategy._pf_udt_Second.get(strategy.second).data.put("value", 2);
    strategy.restore_script_state();
    if (strategy._pf_udt_First.get(strategy.first).data.contains("value")) return 3;
    if (strategy._pf_udt_Second.get(strategy.second).data.contains("value")) return 4;

    strategy.snapshot_script_state();
    strategy._pf_udt_Second.get(strategy.second).data.put("reverse", 3);
    strategy._pf_udt_First.get(strategy.first).data.put("reverse", 4);
    strategy.restore_script_state();
    if (strategy._pf_udt_First.get(strategy.first).data.contains("reverse")) return 5;
    if (strategy._pf_udt_Second.get(strategy.second).data.contains("reverse")) return 6;

    strategy._pf_udt_First.get(strategy.first).data.put("shared", 9);
    if (strategy._pf_udt_Second.get(strategy.second).data.get("shared") != 9) return 7;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-cross-type-alias-rollback",
    ) == "ok\n"


def test_udt_arena_checkpoint_is_lazy_under_real_coof_schedule_runtime() -> None:
    source = r'''//@version=6
strategy("UDT lazy COOF checkpoint", calc_on_order_fills=true)
type Item
    float value
Item current = Item.new(close)
observed = current.value
'''
    cpp = transpile(source)
    arena_trait = cpp.split(
        "struct _PFCheckpointTraits<_PFUdtArena<Item, _PFUdtRecord_Item>>",
        1,
    )[1].split("};", 1)[0]
    assert "using snapshot_type = typename arena_type::Snapshot;" in arena_trait
    assert "return value.snapshot();" in arena_trait
    assert "for (" not in arena_trait
    driver = r'''
#include <iostream>
#include <vector>
int main() {
    GeneratedStrategy strategy;
    std::vector<Bar> bars;
    bars.reserve(2000);
    for (int index = 0; index < 2000; ++index) {
        const double value = 100.0 + static_cast<double>(index);
        bars.push_back(Bar{value, value, value, value, 1.0, index});
    }
    strategy.run(bars.data(), bars.size());
    if (!strategy.last_error().empty()) return 2;
    if (strategy._pf_udt_Item.size() != bars.size()) return 3;
    if (strategy.observed != bars.back().close) return 4;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-lazy-coof-checkpoint",
    ) == "ok\n"


def test_udt_scalar_reads_do_not_log_but_writes_still_rollback_runtime() -> None:
    source = r'''//@version=6
strategy("UDT read-only journal")
type Item
    float value
var Item holder = Item.new(1.0)
observed = holder.value
'''
    cpp = transpile(source)
    assert "observed = _pf_udt_Item.read(holder).value;" in cpp
    assert "_pf_udt_Item.get(holder).value" not in cpp
    driver = r'''
#include <iostream>
#include <type_traits>
static_assert(!std::is_copy_constructible_v<GeneratedStrategy>);
static_assert(!std::is_move_constructible_v<GeneratedStrategy>);
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;

    strategy.snapshot_script_state();
    if (strategy._pf_udt_Item.read(strategy.holder).value != 1.0) return 3;
    if (!strategy._pf_udt_undo.empty()) return 4;
    strategy._pf_udt_Item.get(strategy.holder).value = 9.0;
    if (strategy._pf_udt_undo.empty()) return 5;
    strategy.restore_script_state();
    if (strategy._pf_udt_Item.read(strategy.holder).value != 1.0) return 6;
    if (!strategy._pf_udt_undo.empty()) return 7;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-read-only-journal",
    ) == "ok\n"


_ARRAY_OF_UDT_SOURCE = r'''//@version=6
strategy("Array of UDT handles")
type Item
    float value
var array<Item> items = array.from(Item.new(1.0))
Item selected = array.get(items, 0)
selected.value += 1.0
for item in items
    item.value += 1.0
    item := Item.new(99.0)
observed = items.get(0).value
'''


def test_array_elements_and_for_in_bind_udt_handles_by_value_runtime() -> None:
    cpp = transpile(_ARRAY_OF_UDT_SOURCE)
    assert "for (auto item : items)" in cpp
    assert "for (auto& item : items)" not in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.observed != 3.0) return 3;
    if (strategy._pf_udt_Item.get(strategy.items.at(0)).value != 3.0) return 4;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="array-of-udt-handles-runtime",
    ) == "ok\n"


def test_udt_arena_growth_keeps_existing_record_references_stable_runtime() -> None:
    source = r'''//@version=6
strategy("UDT stable arena records")
type Item
    float value
var Item holder = Item.new(1.0)
observed = holder.value
'''
    cpp = transpile(source)
    assert "std::deque<_PFSlot> _pf_records_;" in cpp
    assert "UDT object-ID capacity exceeded" in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    auto& borrowed = strategy._pf_udt_Item.get(strategy.holder);
    for (int index = 0; index < 4096; ++index) {
        _PFUdtRecord_Item extra{};
        extra.value = static_cast<double>(index);
        strategy._pf_udt_Item.create(extra);
    }
    borrowed.value = 9.0;
    if (strategy._pf_udt_Item.get(strategy.holder).value != 9.0) return 3;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-stable-arena-records",
    ) == "ok\n"


def test_typed_user_copy_method_precedes_builtin_udt_copy() -> None:
    source = r'''//@version=6
strategy("typed copy precedence")
type Item
    float value
method copy(Item self, float replacement) =>
    self.value := replacement
    self
var Item original = Item.new(1.0)
var Item result = original.copy(7.0)
observed = result.value
'''
    cpp = transpile(source)
    assert "_udt_Item_copy(original, 7.0)" in cpp
    assert "result = _pf_udt_Item.copy(original);" not in cpp
    compile_cpp(cpp, label="udt-user-copy-precedence")


def test_forward_typed_user_copy_method_precedes_builtin_runtime() -> None:
    source = r'''//@version=6
strategy("forward typed copy precedence")
type Item
    float value
var Item original = Item.new(1.0)
var Item result = original.copy(7.0)
observed = result.value
method copy(Item self, float replacement) =>
    self.value := replacement
    self
'''
    cpp = transpile(source)
    assert "_udt_Item_copy(original, 7.0)" in cpp
    assert "result = _pf_udt_Item.copy(original);" not in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.observed != 7.0) return 3;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-forward-user-copy-precedence",
    ) == "ok\n"


def test_forward_typed_method_fills_named_and_default_arguments_runtime() -> None:
    source = r'''//@version=6
strategy("forward typed method defaults")
type Item
    float value
var Item item = Item.new(1.0)
var float observed = item.combine(second=4)
method combine(Item self, int first = 2, int second = 3) =>
    first * 10 + second
'''
    cpp = transpile(source)
    assert "observed = _udt_Item_combine(item, 2, 4);" in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.observed != 24.0) return 3;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="udt-forward-method-defaults",
    ) == "ok\n"


def test_forward_stateful_typed_method_default_sizes_ta_runtime() -> None:
    source = r'''//@version=6
strategy("forward stateful typed method default")
out = 1.smooth()
method smooth(int self, int len = 2) =>
    ta.sma(close, len)
'''
    cpp = transpile(source)
    assert "ta::SMA _ta_sma_1;" in cpp
    assert "explicit GeneratedStrategy() : _ta_sma_1(2)" in cpp
    assert "out = _udt_int_smooth_cs0(1, 2);" in cpp
    compile_cpp(cpp, label="forward-stateful-method-default")


def test_forward_sibling_method_return_type_reconciles_runtime() -> None:
    source = r'''//@version=6
strategy("forward sibling method return")
type Holder
    float value
method outer(Holder self) =>
    self.inner()
method inner(Holder self) =>
    "ok"
var Holder holder = Holder.new(1.0)
result = holder.outer()
'''
    cpp = transpile(source)
    assert "std::string _udt_Holder_outer(Holder self)" in cpp
    assert "std::string _udt_Holder_inner(Holder self)" in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.result != "ok") return 3;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="forward-sibling-method-return",
    ) == "ok\n"


@pytest.mark.parametrize("forward", [False, True])
@pytest.mark.parametrize(
    "call, message",
    [
        (
            "original.edit(1.0, 2.0)",
            "Item.edit: too many positional arguments (expected 1, got 2)",
        ),
        (
            "original.edit(unknown=1.0)",
            "Item.edit: unknown keyword argument 'unknown'",
        ),
        (
            "original.edit(1.0, replacement=2.0)",
            "Item.edit: argument 'replacement' passed both positionally and by keyword",
        ),
        (
            "original.edit()",
            "Item.edit: missing required argument 'replacement'",
        ),
    ],
)
def test_typed_method_invalid_bindings_fail_in_analyzer(
    forward: bool,
    call: str,
    message: str,
) -> None:
    declaration = r'''method edit(Item self, float replacement) =>
    self.value := replacement
    self
'''
    call_site = f"var Item invalid = {call}\n"
    body = call_site + declaration if forward else declaration + call_site
    source = f'''//@version=6
strategy("strict typed method binding")
type Item
    float value
var Item original = Item.new(1.0)
{body}'''
    with pytest.raises(CompileError) as raised:
        transpile(source)
    assert raised.value.diagnostics[0].phase is Phase.ANALYZER
    assert raised.value.diagnostics[0].message == message


@pytest.mark.parametrize(
    "call, message",
    [
        ("original.copy(1.0)", r"Item\.copy\(\) expects no arguments"),
        ("Item.copy(original, original)", r"Item\.copy\(\.\.\.\) expects exactly one"),
    ],
)
def test_builtin_udt_copy_rejects_wrong_arity(call: str, message: str) -> None:
    source = f'''//@version=6
strategy("UDT copy arity")
type Item
    float value
var Item original = Item.new(1.0)
var Item invalid = {call}
'''
    with pytest.raises(CompileError, match=message):
        transpile(source)


@pytest.mark.parametrize("copy_expr", ["original.copy()", "Bag.copy(original)"])
def test_udt_copy_with_direct_array_field_fails_closed(copy_expr: str) -> None:
    source = f'''//@version=6
strategy("UDT array field copy boundary")
type Bag
    array<int> values
var Bag original = Bag.new(array.from(1, 2))
var Bag invalid = {copy_expr}
'''
    with pytest.raises(
        CompileError,
        match=r"Bag\.copy\(\) is unsupported because direct array field\(s\) values",
    ):
        transpile(source)


def test_udt_generated_record_name_avoids_authored_type_collision() -> None:
    source = r'''//@version=6
strategy("UDT record name collision")
type Item
    float value
type _PFUdtRecord_Item
    float other
var Item item = Item.new(1.0)
var _PFUdtRecord_Item authored = _PFUdtRecord_Item.new(2.0)
observed = item.value + authored.other
'''
    cpp = transpile(source)
    assert "struct _PFUdtRecord_Item__pf2" in cpp
    assert "struct _PFUdtRecord_Item {\n    int32_t __pf_id" in cpp
    compile_cpp(cpp, label="udt-record-name-collision")


def test_udt_generated_arena_member_avoids_authored_binding_collision() -> None:
    source = r'''//@version=6
strategy("UDT arena member collision")
type Item
    float value
var Item _pf_udt_Item = Item.new(1.0)
var Item alias = _pf_udt_Item
alias.value := 3.0
observed = _pf_udt_Item.value
'''
    cpp = transpile(source)
    assert (
        "_PFUdtArena<Item, _PFUdtRecord_Item> "
        "_pf_udt_Item__pf2{&_pf_udt_undo};"
    ) in cpp
    assert "Item _pf_udt_Item;" in cpp
    assert "_pf_udt_Item__pf2.get(alias).value = 3.0;" in cpp
    compile_cpp(cpp, label="udt-arena-member-collision")


def test_udt_generated_support_templates_avoid_authored_type_collisions() -> None:
    source = r'''//@version=6
strategy("UDT support template collisions")
type _PFUdtArena
    float value
type _PFCheckpointTraits
    float value
var _PFUdtArena first = _PFUdtArena.new(1.0)
var _PFCheckpointTraits checkpoint_obj = _PFCheckpointTraits.new(2.0)
observed = first.value + checkpoint_obj.value
'''
    cpp = transpile(source)
    assert "class _PFUdtArena__pf2" in cpp
    assert "struct _PFCheckpointTraits__pf2" in cpp
    assert "_PFUdtArena__pf2<_PFUdtArena," in cpp
    assert "_PFCheckpointTraits__pf2<decltype(" in cpp
    compile_cpp(cpp, label="udt-support-template-collisions")


def test_udt_generated_record_avoids_enum_string_table_collision() -> None:
    source = r'''//@version=6
strategy("enum table record collision")
enum _PFUdtRecord_X
    one = "one"
type X_str_values
    float value
var X_str_values item = X_str_values.new(2.0)
choice = _PFUdtRecord_X.one
observed = item.value
labelText = str.tostring(choice)
'''
    cpp = transpile(source)
    assert "struct _PFUdtRecord_X_str_values__pf2" in cpp
    assert "static const std::string _PFUdtRecord_X_str_values[]" in cpp
    assert (
        "_PFUdtArena<X_str_values, _PFUdtRecord_X_str_values__pf2>"
        in cpp
    )
    compile_cpp(cpp, label="udt-record-enum-table-collision")


def test_udt_checkpoint_traits_avoids_authored_binding_hiding() -> None:
    source = r'''//@version=6
strategy("trait binding collision")
type Item
    float value
var float _PFCheckpointTraits = 4.0
var Item item = Item.new(2.0)
observed = _PFCheckpointTraits + item.value
'''
    cpp = transpile(source)
    assert "struct _PFCheckpointTraits__pf2;" in cpp
    assert "struct _PFCheckpointTraits__pf2 {" in cpp
    assert "double _PFCheckpointTraits;" in cpp
    assert (
        "_PFCheckpointTraits__pf2<"
        "decltype(GeneratedStrategy::_pf_udt_undo)>"
    ) in cpp
    compile_cpp(cpp, label="udt-trait-binding-hiding")


def test_udt_generated_record_avoids_authored_udf_hiding() -> None:
    source = r'''//@version=6
strategy("record UDF collision")
type Item
    float value
_PFUdtRecord_Item(float value) =>
    value + 1.0
var Item item = Item.new(2.0)
observed = _PFUdtRecord_Item(item.value)
'''
    cpp = transpile(source)
    assert "struct _PFUdtRecord_Item__pf2" in cpp
    assert "_PFUdtArena<Item, _PFUdtRecord_Item__pf2>" in cpp
    assert "double _PFUdtRecord_Item(double value)" in cpp
    compile_cpp(cpp, label="udt-record-udf-hiding")


def test_udt_arena_member_avoids_callable_parameter_hiding() -> None:
    source = r'''//@version=6
strategy("arena parameter collision")
type Item
    float value
read_value(Item item, float _pf_udt_Item) =>
    item.value + _pf_udt_Item
var Item item = Item.new(2.0)
observed = read_value(item, 3.0)
'''
    cpp = transpile(source)
    assert "_pf_udt_Item__pf2{&_pf_udt_undo};" in cpp
    assert "double read_value(Item item, double _pf_udt_Item)" in cpp
    assert (
        "_pf_udt_Item__pf2.read(item).value + _pf_udt_Item"
        in cpp
    )
    compile_cpp(cpp, label="udt-arena-parameter-hiding")


def test_udt_generated_name_skips_an_occupied_pf2_suffix() -> None:
    source = r'''//@version=6
strategy("occupied suffix collision")
type _PFUdtArena
    float value
type _PFUdtArena__pf2
    float value
var _PFUdtArena first = _PFUdtArena.new(1.0)
var _PFUdtArena__pf2 other = _PFUdtArena__pf2.new(2.0)
observed = first.value + other.value
'''
    cpp = transpile(source)
    assert "class _PFUdtArena__pf3 {" in cpp
    assert "class _PFUdtArena__pf2 {" not in cpp
    compile_cpp(cpp, label="udt-support-occupied-suffix")


def test_udt_arena_member_avoids_natural_callable_clone_name() -> None:
    source = r'''//@version=6
strategy("natural clone collision")
type X_cs1
    float value
_pf_udt_X() =>
    var float state = 0.0
    state += 1.0
    state
a = _pf_udt_X()
b = _pf_udt_X()
var X_cs1 item = X_cs1.new(3.0)
observed = item.value + a + b
'''
    cpp = transpile(source)
    assert "double _pf_udt_X_cs1()" in cpp
    assert (
        "_PFUdtArena<X_cs1, _PFUdtRecord_X_cs1> "
        "_pf_udt_X_cs1__pf2{&_pf_udt_undo};"
    ) in cpp
    compile_cpp(cpp, label="udt-arena-natural-clone-collision")


def test_udt_arena_member_avoids_fresh_callable_instance_name() -> None:
    source = r'''//@version=6
strategy("fresh clone collision")
type X__ni1
    float value
_pf_udt_X(int size) =>
    ta.highest(size)
f_get(int len) => _pf_udt_X(len)
g_get(int len) => _pf_udt_X(len)
a = f_get(10)
b = f_get(20)
c = g_get(30)
var X__ni1 item = X__ni1.new(3.0)
observed = item.value + a + b + c
'''
    cpp = transpile(source)
    assert "double _pf_udt_X__ni1(int size)" in cpp
    assert (
        "_PFUdtArena<X__ni1, _PFUdtRecord_X__ni1> "
        "_pf_udt_X__ni1__pf2{&_pf_udt_undo};"
    ) in cpp
    compile_cpp(cpp, label="udt-arena-fresh-instance-collision")
