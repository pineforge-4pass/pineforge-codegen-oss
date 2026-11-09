"""Creation-bar array bindings, pinned to nine observed TradingView behaviours.

The nine bodies retain the original scripts' statement order and quantity
calculations. The driver observes raw sizes, not the quantity code (whose
branches overlap on early bars). Expected sizes below are literals taken from
those observations. Two rows are guards that pass without the change: one does
not separate construction from the end of the creation bar, and one builds its
record on every bar. Every holder keeps its own snapshot: two records built
from one array, and two fields of one record given the same array, do not
share it after the creation bar.
"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu
from tests._e2e import reference_codegen


HEADER = '''//@version=6
strategy("retained arrays", overlay = true, pyramiding = 0, default_qty_type = strategy.fixed, default_qty_value = 1, initial_capital = 1000000000, process_orders_on_close = false)
'''
TRADES = '''if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = code)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
'''

EMPTY_SOURCE = HEADER + '''type Book
    array<int> stamps
var source = array.new<int>()
var b = Book.new(source)
source.push(time)
float x = b.stamps.size()
float live = bar_index + 1.0
float code = bar_index == 0 ? 4.0 : math.abs(x - live) < 0.000001 ? 1.0 : math.abs(x - 0.0) < 0.000001 ? 2.0 : math.abs(x - 1.0) < 0.000001 ? 3.0 : 4.0
''' + TRADES + '''observed = b.stamps.size()
observedSource = source.size()
'''

TWO_FIRST_BAR_PUSHES = HEADER + '''type Book
    array<int> stamps
method countStamps(Book self) =>
    self.stamps.size()
var source = array.new<int>()
var b = Book.new(source)
source.push(bar_index)
if bar_index == 0
    source.push(1)
int actual = b.countStamps()
float code = bar_index == 0 ? 4.0 : actual == bar_index + 2 ? 1.0 : actual == 0 ? 2.0 : actual == 2 ? 3.0 : 4.0
''' + TRADES + '''observed = b.stamps.size()
observedSource = source.size()
'''

LATER_BAR_RECORD = HEADER + '''type Book
    array<int> stamps
var source = array.new<int>()
var Book b = na
source.push(bar_index)
if bar_index == 3
    b := Book.new(source)
float s = na(b) ? -1.0 : b.stamps.size()
float code = s == bar_index + 1.0 ? 1.0 : s == 3.0 ? 2.0 : s == 4.0 ? 3.0 : 4.0
''' + TRADES + '''observed = s
observedSource = source.size()
'''

LATER_BAR_PUSH_AFTER = HEADER + '''type Book
    array<int> stamps
var source = array.new<int>()
var Book b = na
source.push(bar_index)
if bar_index == 3
    b := Book.new(source)
    source.push(100)
float s = na(b) ? -1.0 : b.stamps.size()
float code = s == bar_index + 2.0 ? 1.0 : s == 4.0 ? 2.0 : s == 5.0 ? 3.0 : 4.0
''' + TRADES + '''observed = s
observedSource = source.size()
'''

PER_BAR_RECORD = HEADER + '''type Book
    array<int> stamps
var source = array.new<int>()
source.push(bar_index)
b = Book.new(source)
source.push(bar_index)
float s = b.stamps.size()
float code = s == source.size() ? 1.0 : s == source.size() - 1.0 ? 2.0 : 4.0
''' + TRADES + '''observed = s
observedSource = source.size()
'''

FIELD_WRITES = HEADER + '''type Book
    array<int> stamps
var source = array.new<int>()
var b = Book.new(source)
b.stamps.push(bar_index)
float bs = b.stamps.size()
float ss = source.size()
float code = bs == bar_index + 1.0 and ss == bar_index + 1.0 ? 1.0 : bs == bar_index + 1.0 and ss == 1.0 ? 3.0 : 4.0
''' + TRADES + '''observed = bs
observedSource = ss
'''

PLAIN_ALIAS = HEADER + '''var a = array.new<int>()
var c = a
a.push(bar_index)
float s = c.size()
float code = s == bar_index + 1.0 ? 1.0 : s == 0.0 ? 2.0 : s == 1.0 ? 3.0 : 4.0
''' + TRADES + '''observed = s
observedSource = a.size()
'''

TWO_RECORDS_ONE_SOURCE = HEADER + '''type Bag
    array<int> values
var items = array.new<int>()
var keepA = Bag.new(items)
var keepB = Bag.new(items)
if bar_index == 0
    items.push(7)
else
    keepA.values.push(bar_index)
float bs = keepB.values.size()
float code = bs == bar_index + 1.0 ? 1.0 : bs == 1.0 ? 2.0 : 4.0
''' + TRADES + '''observed = bs
observedSource = keepA.values.size()
'''

TWO_FIELDS_ONE_SOURCE = HEADER + '''type Pair
    array<int> lhs
    array<int> rhs
var items = array.new<int>()
var keep = Pair.new(items, items)
if bar_index == 0
    items.push(7)
else
    keep.lhs.push(bar_index)
float bs = keep.rhs.size()
float code = bs == bar_index + 1.0 ? 1.0 : bs == 1.0 ? 2.0 : 4.0
''' + TRADES + '''observed = bs
observedSource = keep.lhs.size()
'''

DRIVER = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bars[11];
    for (int i = 0; i < 11; ++i)
        bars[i] = {100.0, 104.0, 99.0, 103.0, 10.0,
                   1743466500000LL + i * 900000LL};
    // Reusing the strategy also exercises the generated run reset.
    for (int n : {8, 11}) {
        strategy.run(bars, n);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << strategy.observed << ' ' << strategy.observedSource << '\n';
    }
}
'''


def _observe(source):
    return run_emitted_tu(
        transpile(source), DRIVER, opt="-O0", label="retained array alias",
    )


@pytest.mark.parametrize("source, expected", [
    pytest.param(EMPTY_SOURCE, "1 8\n1 11\n", id="empty-source-first-bar"),
    pytest.param(TWO_FIRST_BAR_PUSHES, "2 9\n2 12\n", id="two-pushes-first-bar"),
    pytest.param(LATER_BAR_RECORD, "4 8\n4 11\n", id="later-bar-guard"),
    pytest.param(LATER_BAR_PUSH_AFTER, "5 9\n5 12\n", id="later-bar-push-after"),
    pytest.param(PER_BAR_RECORD, "16 16\n22 22\n", id="per-bar-record-guard"),
    pytest.param(FIELD_WRITES, "8 1\n11 1\n", id="field-write-persistence"),
    pytest.param(PLAIN_ALIAS, "1 8\n1 11\n", id="plain-var-alias"),
    # The second number is the first holder's size after the pushes made
    # through it; the first is the other holder, which keeps its own snapshot.
    pytest.param(TWO_RECORDS_ONE_SOURCE, "1 8\n1 11\n", id="two-records-one-source"),
    pytest.param(TWO_FIELDS_ONE_SOURCE, "1 8\n1 11\n", id="two-fields-one-source"),
])
def test_creation_bar_sizes(source, expected):
    assert _observe(source) == expected


NO_LATER_PUSH = HEADER + '''type Bag
    array<int> values
var items = array.from(7, 9)
var bag = Bag.new(items)
observed = bag.values.size()
observedSource = items.size()
'''
LITERAL = HEADER + '''type Bag
    array<int> values
var bag = Bag.new(array.from(7, 9))
observed = bag.values.size()
observedSource = bag.values.get(0)
'''


@pytest.mark.parametrize("source, expected", [
    pytest.param(NO_LATER_PUSH, "2 2\n2 2\n", id="no-post-construction-push"),
    pytest.param(LITERAL, "2 7\n2 7\n", id="array-literal"),
])
def test_unchanged_neighbour_sizes(source, expected):
    assert _observe(source) == expected


PLAIN_ALIAS_READS = HEADER + '''var a = array.new<int>()
var c = a
a.push(bar_index + 10)
float s1 = c.size()
float s2 = array.size(c)
float g1 = c.get(0)
float g2 = array.get(c, 0)
float t = 0.0
for v in c
    t += v
observed = s1 + 10 * s2 + 100 * (g1 + g2 + t + c.last())
observedSource = a.size()
'''


def test_plain_alias_supports_the_usual_array_reads():
    # The alias holds the array as it stood at the end of bar 0: one element, 10.
    # size 1, array.size 1, get 10, array.get 10, loop sum 10, last 10:
    # 1 + 10 * 1 + 100 * (10 + 10 + 10 + 10) = 4011.
    assert _observe(PLAIN_ALIAS_READS) == "4011 8\n4011 11\n"


COPY_IN_CREATION_BAR = HEADER + '''type Bag
    array<int> values
var items = array.new<int>()
var bag = Bag.new(items)
var copied = {copy_call}
if bar_index == 0
    items.push(7)
else
    copied.values.push(bar_index)
observed = bag.values.size()
observedSource = items.size()
'''
COPY_IN_LATER_BAR = HEADER + '''type Bag
    array<int> values
var items = array.new<int>()
var bag = Bag.new(items)
var Bag copied = na
items.push(bar_index)
if bar_index == 3
    copied := bag.copy()
if bar_index > 3
    copied.values.push(bar_index)
observed = bag.values.size()
observedSource = items.size()
'''


@pytest.mark.parametrize("source", [
    pytest.param(COPY_IN_CREATION_BAR.format(copy_call="bag.copy()"),
                 id="method-in-creation-bar"),
    pytest.param(COPY_IN_CREATION_BAR.format(copy_call="Bag.copy(bag)"),
                 id="namespace-in-creation-bar"),
    pytest.param(COPY_IN_LATER_BAR, id="method-in-later-bar"),
])
def test_copy_of_a_record_with_a_retained_array_field_is_refused(source):
    # TradingView does not share the array of a record with the array of its
    # copy (pushes made through the copy never reached the original, whether
    # the copy is made in the creation bar or in a later one), and no
    # observation pins what the copy itself holds. The compile error of the
    # base build stays.
    with pytest.raises(
        CompileError,
        match=r"Bag\.copy\(\) is unsupported because direct array field\(s\) values",
    ):
        transpile(source)


def test_pending_and_frozen_checkpoint_restore_binding_identity_and_contents():
    # This is a unit of the generated trait, not an additional Pine semantic
    # claim about intrabar recalculation (which retains its old lowering).
    driver = r'''
#include <iostream>
int main() {
    std::vector<int> source{7};
    _PFRetainedArrayManager<int> manager;
    auto field = manager.pending(source);
    auto shallow = field;
    using Traits = _PFCheckpointTraits<decltype(field)>;
    using SourceTraits = _PFCheckpointTraits<decltype(source)>;
    using ManagerTraits = _PFCheckpointTraits<decltype(manager)>;
    auto pending = Traits::take(field);
    auto source_before = SourceTraits::take(source);
    auto manager_before = ManagerTraits::take(manager);
    source.push_back(8);
    manager.close();
    if ((*field).size() != 2 || (*shallow).size() != 2) return 2;
    Traits::restore(field, pending);
    SourceTraits::restore(source, source_before);
    ManagerTraits::restore(manager, manager_before);
    if (field.binding()->pending != &source || field.binding()->owned) return 3;
    if ((*shallow).size() != 1) return 4;
    source.push_back(9);
    manager.close();
    if ((*field).size() != 2 || field.binding()->pending) return 5;
    auto frozen = Traits::take(field);
    (*shallow).push_back(10);
    source.push_back(11);
    auto later = manager.pending(source);
    Traits::restore(field, frozen);
    if ((*field).size() != 2 || (*shallow).size() != 2) return 6;
    if (later.binding() == field.binding() || (*later).size() != 3) return 7;
    manager.close();
    source.push_back(12);
    if ((*later).size() != 3 || (*field).size() != 2) return 8;
    std::cout << "ok\n";
}
'''
    assert run_emitted_tu(
        transpile(TWO_FIRST_BAR_PUSHES), driver, opt="-O0", label="retained binding checkpoint",
    ) == "ok\n"


def test_generated_checkpoint_restores_retained_field_and_source():
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bar{100.0, 104.0, 99.0, 103.0, 10.0, 1743466500000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    strategy.snapshot_script_state();
    auto original = strategy._pf_udt_Book.read(strategy.b).stamps;
    auto& field = strategy._pf_udt_Book.get(strategy.b).stamps;
    (*field).push_back(33);
    strategy.pool.push_back(44);
    field = strategy._pf_retained_pool.pending(strategy.pool);
    strategy._pf_retained_pool.close();
    strategy.restore_script_state();
    const auto restored = strategy._pf_udt_Book.read(strategy.b).stamps;
    if ((*restored).size() != 2 || strategy.pool.size() != 2) return 3;
    if (restored.binding() != original.binding()) return 4;
    if (restored.binding()->pending) return 5;
    std::cout << "ok\n";
}
'''
    # The array is named pool here: the name source is escaped in the generated
    # class, and the driver reaches the members by their emitted names.
    assert run_emitted_tu(
        transpile(TWO_FIRST_BAR_PUSHES.replace("source", "pool")), driver, opt="-O0",
        label="retained generated checkpoint",
    ) == "ok\n"


BASE = "db32112f46ecf351c1c21755c33d8a6531cc8160"


def _reference_cpp(source):
    """Only use a reachable ancestor; otherwise the caller pins tokens."""
    repo = Path(__file__).resolve().parents[1]
    reachable = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", BASE, "HEAD"],
        capture_output=True,
    )
    if reachable.returncode:
        return None
    tree = reference_codegen(BASE)
    if tree is None:
        return None
    env = dict(os.environ, PYTHONPATH=str(tree))
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; from pineforge_codegen import transpile; "
         "sys.stdout.write(transpile(sys.stdin.read()))"],
        input=source, text=True, capture_output=True, cwd=tree, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("source", [PER_BAR_RECORD, LITERAL])
def test_unselected_neighbours_keep_base_emission(source):
    cpp = transpile(source)
    reference = _reference_cpp(source)
    if reference is not None:
        assert cpp == reference
    elif source == PER_BAR_RECORD:
        assert "_PFArrayField<int>::alias(source)" in cpp
        assert "_PFRetainedArray" not in cpp
    else:
        assert "std::vector<int> stamps" not in cpp
        assert "std::vector<int> values" in cpp
        assert "_PFRetainedArray" not in cpp


@pytest.mark.parametrize("source", [
    pytest.param(NO_LATER_PUSH + "copied = items.copy()\n", id="array-copy"),
    pytest.param(NO_LATER_PUSH + "previous = items[1]\n", id="array-history"),
    pytest.param(NO_LATER_PUSH + '''type Envelope
    Bag nested
var wrapped = Envelope.new(bag)
''', id="nested-record"),
    pytest.param(NO_LATER_PUSH.replace(
        "var bag =", "varip bag ="), id="varip"),
    pytest.param(NO_LATER_PUSH.replace(
        "pyramiding = 0,", "pyramiding = 0, calc_on_order_fills = true,"),
        id="intrabar-recalculation"),
    pytest.param(NO_LATER_PUSH + "items := array.from(1)\n", id="source-rebind"),
    pytest.param(HEADER + '''var items = array.new<int>()
var alias = items
var chained = alias
observed = chained.size()
''', id="alias-chain"),
])
def test_unmeasured_lifetimes_keep_base_emission(source):
    cpp = transpile(source)
    assert "_PFRetainedArray" not in cpp
    reference = _reference_cpp(source)
    if reference is not None:
        assert cpp == reference
