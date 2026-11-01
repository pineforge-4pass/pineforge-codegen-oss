"""Map and matrix checkpoint costs, ordered rollback and handle identity."""

from __future__ import annotations

import json

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import base, tables
from pineforge_codegen.collection_history import MATRIX_CHANGING_METHODS
from tests._compile import run_emitted_tu
from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests import test_e2e_security_coof_restore_witness as schedule


HEADER = '//@version=6\nstrategy("collection checkpoints", calc_on_order_fills=true)\n'
MAP = 'var m = map.new<string, int>()\n'
MATRIX = 'var m = matrix.new<int>(2, 2, 0)\n'
MAP_SEED = '''if bar_index == 0
    m.put("a", 2)
    m.put("b", 5)
    m.put("c", 7)
'''
MATRIX_SEED = '''if bar_index == 0
    m.set(0, 0, 1)
    m.set(0, 1, 2)
    m.set(1, 0, 3)
    m.set(1, 1, 4)
'''


def _snapshot(cpp):
    return cpp.split("    void snapshot_script_state() override {\n", 1)[1].split(
        "    void restore_script_state() override {", 1)[0]


def _enrolled(cpp, name="m"):
    # One address/generation token replaces this root's complete payload.
    snapshot = _snapshot(cpp)
    assert f"decltype(GeneratedStrategy::{name})>::take({name})" not in snapshot
    assert f"{{&this->{name}, _pf_next_generation}}" in snapshot
    assert "class _PFGraphJournal" in cpp


def _eager_cpp(source, monkeypatch):
    with monkeypatch.context() as patch:
        module = getattr(base, "graph_journal", None)
        if module is not None:
            patch.setattr(module, "prepare", lambda cg: None)
        return transpile(source)


def _source(declaration, seed, parameters, body, call, extra=""):
    return (HEADER + declaration + seed + extra
            + f"mutate({parameters}) =>\n    " + body.replace("\n", "\n    ") + "\n"
            + "armed = input.bool(false)\nif armed\n    mutate(" + call + ")\n")


def _vector(values, cpp_type="int"):
    return "std::vector<" + cpp_type + ">{" + ", ".join(
        json.dumps(value) if isinstance(value, str) else str(value) for value in values
    ) + "}"


def _with_entry_count(cpp):
    """Expose the actual record count without changing mutations or replay."""
    accessor = "    size_t entry_count() const { return entries_.size(); }\n"
    if accessor not in cpp:
        # Older coordinators expose only empty(); the accessor keeps resource comparisons executable.
        anchor = "    bool empty() const { return entries_.empty(); }\n"
        assert cpp.count(anchor) == 1
        cpp = cpp.replace(anchor, anchor + accessor, 1)
    return cpp


def test_graph_mutation_catalogues_are_closed():
    module = getattr(base, "graph_journal", None)
    delta = getattr(module, "MAP_DELTA", frozenset())
    full = getattr(module, "MAP_FULL", frozenset())
    reads = getattr(module, "MAP_READ", frozenset())
    # Put saves one pair; the other three map writes need ordered full preimages.
    assert delta == {"put"}
    assert full == {"remove", "clear", "put_all"}
    assert delta | full | reads == set(tables.MAP_METHODS)
    assert not (delta | full) & reads
    delta = getattr(module, "MATRIX_DELTA", frozenset())
    full = getattr(module, "MATRIX_FULL", frozenset())
    mixed = getattr(module, "MATRIX_MIXED", frozenset())
    eager = getattr(module, "MATRIX_EAGER", frozenset())
    reads = getattr(module, "MATRIX_READ", frozenset())
    # Set saves one cell; six families have generic deltas, four always save full state, and concat stays eager.
    assert delta == {"set"}
    assert mixed == {"add_row", "add_col", "remove_row", "remove_col", "swap_rows", "swap_columns"}
    assert eager == {"concat"}
    assert delta | full | mixed | eager == MATRIX_CHANGING_METHODS
    assert not delta & full and not mixed & (delta | full | eager)
    assert not (delta | full | mixed | eager) & reads
    assert delta | full | mixed | eager | reads == set(tables.MATRIX_METHODS)


@pytest.mark.parametrize("declaration, operation, boundary", [
    # Each map call binds its receiver once, then records at the selected runtime operation.
    (MAP, 'm.put("a", 2)', "this->_pf_graph_journal.map(m)"),
    (MAP, 'map.put(m, "a", 2)', "this->_pf_graph_journal.map(m)"),
    (MAP, 'm.remove("a")', "this->_pf_graph_journal.map(m)"),
    (MAP, "m.clear()", "this->_pf_graph_journal.map(m)"),
    (MAP + 'other = map.new<string, int>()\n', "m.put_all(other)", "this->_pf_graph_journal.map(m)"),
    # Matrix calls preserve the old argument expressions and defaults around the adapter receiver.
    (MATRIX, "m.set(0, 1, 9)", "this->_pf_graph_journal.matrix(m).set((int)(0), (int)(1), 9)"),
    (MATRIX, "matrix.set(id=m, value=9, column=1, row=0)", "this->_pf_graph_journal.matrix(m).set((int)(0), (int)(1), 9)"),
    (MATRIX, "m.fill(9)", "this->_pf_graph_journal.matrix(m).fill(9)"),
    (MATRIX, "m.remove_row(1)", "this->_pf_graph_journal.matrix(m).remove_row((int)(1))"),
    (MATRIX, "m.remove_col(0)", "this->_pf_graph_journal.matrix(m).remove_col((int)(0))"),
    (MATRIX, "m.swap_rows(0, 1)", "this->_pf_graph_journal.matrix(m).swap_rows((int)(0), (int)(1))"),
    (MATRIX, "m.swap_columns(0, 1)", "this->_pf_graph_journal.matrix(m).swap_columns((int)(0), (int)(1))"),
    (MATRIX, "m.reshape(1, 4)", "this->_pf_graph_journal.matrix(m).reshape((int)(1), (int)(4))"),
    (MATRIX, "m.reverse()", "this->_pf_graph_journal.matrix(m).reverse()"),
    (MATRIX, "m.sort()", "this->_pf_graph_journal.matrix(m).sort(0)"),
    # Index-only additions pass their typed-na row/column through the same proxy.
    (MATRIX, "m.add_row(0)", "_pf_matrix_target.add_row((int)(0),"),
    (MATRIX, "m.add_col(0)", "_pf_matrix_target.add_col((int)(0),"),
    # An explicit row/column array is evaluated by the old template before capture.
    (MATRIX, "m.add_row(0, array.from(7, 9))", "this->_pf_graph_journal.matrix(m).add_row((int)(0),"),
    (MATRIX, "m.add_col(0, array.from(7, 9))", "this->_pf_graph_journal.matrix(m).add_col((int)(0),"),
])
def test_direct_mutations_remove_the_checkpoint_payload(declaration, operation, boundary):
    cpp = transpile(HEADER + declaration + operation + "\n")
    _enrolled(cpp)
    assert cpp.count(boundary) == 1  # Exactly one authored call reaches its mutation boundary.
    assert "this->_pf_graph_journal.replace(m," in cpp


def test_small_writes_do_not_copy_the_untouched_payload():
    cpp = transpile(HEADER + MAP + 'm.put("a", 2)\n')
    _enrolled(cpp)
    put = cpp.split("        V put(K key, V value) {", 1)[1].split("        V remove", 1)[0]
    # Optional presence distinguishes an old na value from absence without cloning other keys.
    assert "if (owner.contains(key)) old.emplace(owner.get(key));" in put
    assert "if (old) (void)handle.put(key, *old);" in put
    assert "else (void)handle.remove(key);" in put
    assert "save_handle" not in put and ".snapshot()" not in put
    cpp = transpile(HEADER + MATRIX + "m.set(0, 1, 9)\n")
    _enrolled(cpp)
    setter = cpp.split("        void set(int row, int column, Value value) {", 1)[1].split(
        "        void fill", 1)[0]
    # A validated set saves only its old typed cell, after the existing argument evaluation.
    assert "Value old = owner.get(row, column);" in setter
    assert "handle.set(row, column, old);" in setter
    assert "save_handle" not in setter and ".snapshot()" not in setter
    assert "std::make_shared<typename H::Snapshot>(handle.snapshot())" in cpp
    assert "size_t entry_count() const { return entries_.size(); }" in cpp
    # Below the delta cap, generic append uses an axis index; numeric and middle-row insertions save full state.
    append = cpp.split("        void add_row(int index, const std::vector<Value>& values) {", 1)[1].split(
        "        void add_col", 1)[0]
    assert "static constexpr bool generic = !std::is_same_v<H, pineforge::PineMatrix>;" in cpp
    assert "index == owner.rows()" in append
    delta_path, full_path = append.split("            journal.save_handle(owner, root);", 1)
    assert "[handle = owner, index]()" in delta_path
    assert "handle.remove_row(index);" in delta_path
    assert "journal.commit_handle_delta(entry);" in delta_path
    assert "snapshot" not in delta_path and "old = owner" not in delta_path
    assert "owner.add_row(index, values);" in full_path


FALLBACKS = [
    # Each excluded variant keeps the literal eager trait call for the same named root.
    (MAP + 'alias = m\nm.put("a", 2)\n', "m"),
    (MAP + 'm.put("a", 2)\nvar int total = 0\nfor [key, value] in m\n    total += value\n', "m"),
    (MAP + 'observe(map<string, int> value) => value.size()\nm.put("a", 2)\nr = observe(m)\n', "m"),
    (MAP + 'm := map.new<string, int>()\nm.put("a", 2)\n', "m"),
    (MAP + 'r = m.size()\n', "m"),
    (MAP + 'unused() => m.put("a", 2)\nr = m.size()\n', "m"),
    (MAP + 'f(int m) => m + 1\nm.put("a", 2)\nr = f(3)\n', "m"),
    (MAP + 'm.put("a", 2)\nf() => m.size()\nr = request.security(syminfo.tickerid, "60", f())\n', "m"),
    (MAP + 'm.put("a", 2)\nx = m.size()\nr = request.security(syminfo.tickerid, "60", x)\n', "m"),
    ('type Item\n    int value\nvar item = Item.new(1)\n' + MAP + 'm.put("a", 2)\n', "m"),
    (MATRIX + 'alias = m\nm.set(0, 0, 9)\n', "m"),
    (MATRIX + 'm.set(0, 0, 9)\nr = (m[1]).rows()\n', "m"),
    (MATRIX + 'other = matrix.new<int>(2, 2, 1)\nm.concat(other)\nm.set(0, 0, 9)\n', "m"),
    (MATRIX + 'observe(matrix<int> value) => value.rows()\nm.set(0, 0, 9)\nr = observe(m)\n', "m"),
    (MATRIX + 'm := matrix.new<int>(1, 1, 7)\nm.set(0, 0, 9)\n', "m"),
    ('type Point\n    int value\nvar m = matrix.new<Point>(1, 1, Point.new(2))\nm.set(0, 0, Point.new(9))\n', "m"),
]


@pytest.mark.parametrize("body, root", FALLBACKS)
def test_exclusions_keep_the_old_checkpoint_text(body, root, monkeypatch):
    # The positive half must lose its eager copy; the paired excluded shape must retain it.
    _enrolled(transpile(HEADER + MAP + 'm.put("a", 2)\n'))
    source = HEADER + body
    cpp = transpile(source)
    assert "_pf_graph_journal" not in cpp
    assert f"            _PFCheckpointTraits<decltype(GeneratedStrategy::{root})>::take({root}),\n" in _snapshot(cpp)
    assert cpp == _eager_cpp(source, monkeypatch)


@pytest.mark.parametrize("flag", ["false", "1 == 2", None])
def test_disabled_fill_recalculation_keeps_eager_graph_output(flag, monkeypatch):
    source = HEADER + MAP + 'm.put("a", 2)\n'
    _enrolled(transpile(source))
    disabled = (source.replace("calc_on_order_fills=true", "calc_on_order_fills=" + flag)
                if flag is not None else source.replace(", calc_on_order_fills=true", ""))
    cpp = transpile(disabled)
    # Literal false, the false equality and an absent declaration retain the eager map snapshot.
    assert "_PFCheckpointTraits<decltype(GeneratedStrategy::m)>::take(m)" in _snapshot(cpp)
    assert cpp == _eager_cpp(disabled, monkeypatch)


def test_varip_and_array_only_sources_keep_their_existing_emission(monkeypatch):
    _enrolled(transpile(HEADER + MAP + 'm.put("a", 2)\n'))
    for source in (
        HEADER + MAP.replace("var ", "varip ") + 'm.put("a", 2)\n',
        HEADER + "var a = array.from(2, 5)\na.push(7)\n",
        HEADER + "f() =>\n    var m = map.new<string, int>()\n    m.put(\"a\", 2)\n    m.size()\nr = f()\n",
    ):
        # Varip, an existing array journal and a callable-local map receive no new graph support.
        cpp = transpile(source)
        assert "_pf_graph_journal" not in cpp
        assert "size_t entry_count() const" not in cpp
        assert cpp == _eager_cpp(source, monkeypatch)


def test_missing_mutation_coverage_keeps_the_entire_root_eager(monkeypatch):
    source = HEADER + MAP + 'm.put("a", 2)\nm.clear()\n'
    _enrolled(transpile(source))
    monkeypatch.setattr(base.graph_journal, "MAP_FULL", base.graph_journal.MAP_FULL - {"clear"})
    cpp = transpile(source)
    # An uncovered clear disqualifies the earlier put as well as the final write.
    assert "_pf_graph_journal" not in cpp
    assert "_PFCheckpointTraits<decltype(GeneratedStrategy::m)>::take(m)" in _snapshot(cpp)


def test_trace_mutations_and_detached_results_keep_complete_coverage():
    source = HEADER + MAP + '''m.put("a", 2)
copied = m.copy()
copied.put("a", 9)
r = request.security(syminfo.tickerid, "60", close)
// @pf-trace removed=m.remove("a")
'''
    cpp = transpile(source)
    _enrolled(cpp)
    # One body put and one trace removal use the coordinator; detached-copy mutation stays ordinary.
    assert cpp.count("this->_pf_graph_journal.map(m)") == 2
    assert "_PFCheckpointTraits<decltype(GeneratedStrategy::copied)>::take(copied)" in _snapshot(cpp)


def test_generated_graph_names_do_not_hide_authored_inputs():
    source = HEADER + MAP + '''_PFGraphJournal = input.int(1)
_pf_graph_journal = input.int(7)
m.put("a", _PFGraphJournal + _pf_graph_journal)
'''
    cpp = transpile(source)
    _enrolled(cpp)
    # Both preferred names are occupied, so their support counterparts get the first free suffix.
    assert "class _PFGraphJournal__pf2" in cpp
    assert "this->_pf_graph_journal__pf2.map(m)" in cpp


MAP_ROWS = [
    # An absent key appends at the tail and reports na as -1; different inputs must change that tail.
    ("insert", "string key, int value", "old = m.put(key, value)\nna(old) ? -1 : old",
     '"d", 9', -1, ["a", "b", "c", "d"], [2, 5, 7, 9], '"e", 11', -1, ["a", "b", "c", "e"], [2, 5, 7, 11]),
    # An overwrite returns 5 or 2 and never moves the overwritten key's position.
    ("overwrite", "string key, int value", "m.put(key, value)",
     '"b", 9', 5, ["a", "b", "c"], [2, 9, 7], '"a", 11', 2, ["a", "b", "c"], [11, 5, 7]),
    # Removing the middle or first key keeps survivors ordered; undo must restore the original position.
    ("remove", "string key", "m.remove(key)",
     '"b"', 5, ["a", "c"], [2, 7], '"a"', 2, ["b", "c"], [5, 7]),
    # Removing an absent key leaves all pairs alone, whereas the second call really removes b.
    ("missing_remove", "string key", "old = m.remove(key)\nna(old) ? -1 : old",
     '"z"', -1, ["a", "b", "c"], [2, 5, 7], '"b"', 5, ["a", "c"], [2, 7]),
    # Clear loses all three pairs, then put creates exactly the supplied new pair.
    ("clear", "string key, int value", "m.clear()\nm.put(key, value)\nm.size()",
     '"d", 9', 1, ["d"], [9], '"e", 11', 1, ["e"], [11]),
    # Bulk put keeps b's position, appends d and then overwrites d with the actual argument.
    ("put_all", "int value", 'm.put_all(other)\nm.put("d", value)\nm.get("d")',
     "9", 9, ["a", "b", "c", "d"], [2, 11, 7, 9], "17", 17, ["a", "b", "c", "d"], [2, 11, 7, 17]),
    # The ordered preimage covers removal and the tail insert; the earlier delta then restores b.
    ("mixed", "int value", 'm.put("b", value)\nm.remove("a")\nm.put("d", value + 1)\nm.size()',
     "9", 3, ["b", "c", "d"], [9, 7, 10], "11", 3, ["b", "c", "d"], [11, 7, 12]),
    # The value argument removes b before the outer put; that preimage covers both writes.
    ("same_owner_operand", "string key", 'old = m.put(key, m.remove("b"))\nna(old) ? -1 : old',
     '"a"', 2, ["a", "c"], [5, 7], '"d"', -1, ["a", "c", "d"], [2, 7, 5]),
]


@pytest.mark.parametrize("row", MAP_ROWS, ids=[row[0] for row in MAP_ROWS])
def test_map_mutations_restore_order_twice_and_after_another_mutation(row):
    """Generated map mutations restore ordered pairs through their original handles."""
    name, params, body, call, result, keys, values, again, result2, keys2, values2 = row
    extra = ('var other = map.new<string, int>()\nother.put("b", 11)\nother.put("d", 13)\n'
             if name == "put_all" else "")
    cpp = transpile(_source(MAP, MAP_SEED, params, body, call, extra))
    _enrolled(cpp)
    driver = f'''
#include <cassert>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    auto independent = strategy.m.copy();
    auto baseline = [&]() {{
        assert((strategy.m.keys() == std::vector<std::string>{{"a", "b", "c"}}));
        assert((strategy.m.values() == std::vector<int>{{2, 5, 7}}));
        assert((held.keys() == std::vector<std::string>{{"a", "b", "c"}}));
        assert((held.values() == std::vector<int>{{2, 5, 7}}));
        assert((independent.values() == std::vector<int>{{2, 5, 7}}));
    }};
    baseline();
    strategy.snapshot_script_state();
    assert(strategy.mutate({call}) == {result});
    assert((strategy.m.keys() == {_vector(keys, "std::string")}));
    assert((strategy.m.values() == {_vector(values)}));
    assert((held.values() == {_vector(values)}));
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.empty());
    strategy.restore_script_state();
    baseline();
    assert(strategy.mutate({again}) == {result2});
    assert((strategy.m.keys() == {_vector(keys2, "std::string")}));
    assert((strategy.m.values() == {_vector(values2)}));
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.empty());
    std::cout << "ok\\n";
}}
'''
    # Both restores recover ordered [('a',2),('b',5),('c',7)] through the original held handle.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="map " + name) == "ok\n"


@pytest.mark.parametrize("count, keys, values", [
    # Four inserts append keys 0..3 with value key+2; draining needs one ordered preimage.
    (4, ["0", "1", "2", "3"], [2, 3, 4, 5]),
    # Eight inserts append keys 0..7; doubling the drain length still needs one preimage.
    (8, ["0", "1", "2", "3", "4", "5", "6", "7"], [2, 3, 4, 5, 6, 7, 8, 9]),
])
def test_map_drain_retains_one_preimage_and_restores_order(count, keys, values):
    seed = (f"if bar_index == 0\n    for index = 0 to {count} - 1\n"
            '        m.put(str.tostring(index), index + 2)\n')
    cpp = transpile(_source(
        MAP, seed, "int count",
        "for index = 0 to count - 1\n    m.remove(str.tostring(index))\nm.size()", str(count),
    ))
    _enrolled(cpp)
    driver = f'''
#include <cassert>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    auto baseline = [&]() {{
        assert((strategy.m.keys() == {_vector(keys, "std::string")}));
        assert((strategy.m.values() == {_vector(values)}));
        assert((held.keys() == {_vector(keys, "std::string")}));
        assert((held.values() == {_vector(values)}));
    }};
    baseline();
    strategy.snapshot_script_state();
    assert(strategy.mutate({count}) == 0);
    assert(strategy.m.size() == 0 && held.size() == 0);
    // The first removal saves the whole ordered seed; subsequent removals save nothing.
    assert(strategy._pf_graph_journal.entry_count() == 1);
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.entry_count() == 0);
    strategy.restore_script_state();
    baseline();
    std::cout << "ok\\n";
}}
'''
    # All keys disappear, then the one preimage restores the literal insertion order through both handles.
    assert run_emitted_tu(_with_entry_count(cpp), driver, opt="-O2", label="map drain") == "ok\n"


@pytest.mark.parametrize("boundary, keys, values, baseline_keys, baseline_values", [
    # Restore recovers a,b,c; removing a then appending a and tail gives b,c,a,tail.
    ("restore", ["b", "c", "a", "tail"], [5, 7, 11, 12], ["a", "b", "c"], [2, 5, 7]),
    # Commit keeps a,c,b,tail; removing a appends it after tail, whose overwrite keeps its position.
    ("commit", ["c", "b", "tail", "a"], [7, 9, 12, 11], ["a", "c", "b", "tail"], [2, 7, 9, 10]),
])
def test_full_preimage_latch_expires_at_each_checkpoint_boundary(
    boundary, keys, values, baseline_keys, baseline_values,
):
    cpp = transpile(_source(
        MAP, MAP_SEED, "string key, int value",
        'm.remove(key)\nm.put(key, value)\nm.put("tail", value + 1)\nm.size()', '"b", 9',
    ))
    _enrolled(cpp)
    driver = f'''
#include <cassert>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    strategy.snapshot_script_state();
    assert(strategy.mutate("b", 9) == 4);
    // Removing b then appending b=9 and tail=10 retains a,c in their original order.
    assert((strategy.m.keys() == std::vector<std::string>{{"a", "c", "b", "tail"}}));
    assert((strategy.m.values() == std::vector<int>{{2, 7, 9, 10}}));
    assert(strategy._pf_graph_journal.entry_count() == 1);
    strategy.{boundary}_script_state();
    auto baseline = [&]() {{
        assert((strategy.m.keys() == {_vector(baseline_keys, "std::string")}));
        assert((strategy.m.values() == {_vector(baseline_values)}));
        assert((held.keys() == {_vector(baseline_keys, "std::string")}));
        assert((held.values() == {_vector(baseline_values)}));
    }};
    baseline();
    assert(strategy._pf_graph_journal.entry_count() == 0);
    assert(strategy.mutate("a", 11) == 4);
    assert((strategy.m.keys() == {_vector(keys, "std::string")}));
    assert((strategy.m.values() == {_vector(values)}));
    assert((held.values() == {_vector(values)}));
    // The new execution needs its own preimage, even when restore kept the generation unchanged.
    assert(strategy._pf_graph_journal.entry_count() == 1);
    strategy.restore_script_state();
    baseline();
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.entry_count() == 0);
    std::cout << "ok\\n";
}}
'''
    # The second restore must recover the selected literal baseline, including order through the held handle.
    assert run_emitted_tu(_with_entry_count(cpp), driver, opt="-O2", label="full preimage " + boundary) == "ok\n"


@pytest.mark.parametrize("value, pairs, first_row, second_row", [
    # Fill 11, set 13, add [14,15] and column [16,17,18], trim and swap columns -> [11,17];[15,18].
    (9, [9, 7, 12], [11, 17], [15, 18]),
    # With value 7 the same actual operations give map b=7,c=7,d=10 and rows [9,15];[13,16].
    (7, [7, 7, 10], [9, 15], [13, 16]),
])
def test_deltas_before_full_preimages_replay_in_reverse_across_roots(value, pairs, first_row, second_row):
    cpp = transpile(_source(
        MAP + "var grid = matrix.new<int>(2, 2, 0)\n",
        MAP_SEED + MATRIX_SEED.replace("m.", "grid."), "int value",
        'm.put("b", value)\ngrid.set(0, 0, value + 1)\n'
        'm.remove("a")\ngrid.fill(value + 2)\nm.put("d", value + 3)\n'
        "grid.set(1, 1, value + 4)\ngrid.swap_rows(0, 1)\n"
        "grid.add_row(grid.rows(), array.from(value + 5, value + 6))\n"
        "grid.add_col(1, array.from(value + 7, value + 8, value + 9))\n"
        "grid.remove_row(0)\ngrid.remove_col(0)\ngrid.swap_columns(0, 1)\nm.size()", str(value),
    ))
    _enrolled(cpp)
    _enrolled(cpp, "grid")
    driver = f'''
#include <cassert>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    auto grid = strategy.grid;
    strategy.snapshot_script_state();
    assert(strategy.mutate({value}) == 3);
    assert((held.keys() == std::vector<std::string>{{"b", "c", "d"}}));
    assert((held.values() == {_vector(pairs)}));
    assert(grid.rows() == 2 && grid.columns() == 2);
    assert((grid.row(0) == {_vector(first_row)}));
    assert((grid.row(1) == {_vector(second_row)}));
    // One pair delta, one cell delta and their two later full preimages are the entire log.
    assert(strategy._pf_graph_journal.entry_count() == 4);
    strategy.restore_script_state();
    // Full preimages recover the intermediate writes before the older deltas recover 5 and 1.
    assert((strategy.m.keys() == std::vector<std::string>{{"a", "b", "c"}}));
    assert((strategy.m.values() == std::vector<int>{{2, 5, 7}}));
    assert((held.keys() == std::vector<std::string>{{"a", "b", "c"}}));
    assert((held.values() == std::vector<int>{{2, 5, 7}}));
    assert(strategy.grid.rows() == 2 && strategy.grid.columns() == 2);
    assert((strategy.grid.row(0) == std::vector<int>{{1, 2}}));
    assert((strategy.grid.row(1) == std::vector<int>{{3, 4}}));
    assert((grid.row(0) == std::vector<int>{{1, 2}}));
    assert((grid.row(1) == std::vector<int>{{3, 4}}));
    assert(strategy._pf_graph_journal.entry_count() == 0);
    std::cout << "ok\\n";
}}
'''
    # Replaying the two snapshots before the earlier deltas recovers both complete literal seeds.
    assert run_emitted_tu(_with_entry_count(cpp), driver, opt="-O2", label="deltas before full preimages") == "ok\n"


def test_dense_writes_switch_to_one_full_preimage_per_root():
    cpp = transpile(_source(
        MAP + "var grid = matrix.new<int>(1, 3, 2)\n", MAP_SEED, "int count, int value",
        'for index = 0 to count - 1\n    m.put("a", value + index)\n'
        '    grid.set(0, 0, value + index + 1)\nm.get("a")', "8, 9",
    ))
    _enrolled(cpp)
    _enrolled(cpp, "grid")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    auto grid = strategy.grid;
    auto baseline = [&]() {
        assert((strategy.m.keys() == std::vector<std::string>{"a", "b", "c"}));
        assert((held.values() == std::vector<int>{2, 5, 7}));
        assert(grid.rows() == 1 && grid.columns() == 3);
        assert((grid.row(0) == std::vector<int>{2, 2, 2}));
    };
    strategy.snapshot_script_state();
    // Three writes per three-element root remain six deltas; the last writes are 11 and 12.
    assert(strategy.mutate(3, 9) == 11);
    assert((held.values() == std::vector<int>{11, 5, 7}));
    assert((grid.row(0) == std::vector<int>{12, 2, 2}));
    assert(strategy._pf_graph_journal.entry_count() == 6);
    // The fourth write takes one full preimage per root before writing 15 and 16.
    assert(strategy.mutate(1, 15) == 15);
    assert((held.values() == std::vector<int>{15, 5, 7}));
    assert((grid.row(0) == std::vector<int>{16, 2, 2}));
    assert(strategy._pf_graph_journal.entry_count() == 8);
    // Four more writes end at 24 and 25 and retain the same six deltas plus two full preimages.
    assert(strategy.mutate(4, 21) == 24);
    assert((held.values() == std::vector<int>{24, 5, 7}));
    assert((grid.row(0) == std::vector<int>{25, 2, 2}));
    assert(strategy._pf_graph_journal.entry_count() == 8);
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.entry_count() == 0);
    // Restore clears both latches and delta counts, so eight fresh writes end at 14 and 15 with eight records.
    assert(strategy.mutate(8, 7) == 14);
    assert((held.values() == std::vector<int>{14, 5, 7}));
    assert((grid.row(0) == std::vector<int>{15, 2, 2}));
    assert(strategy._pf_graph_journal.entry_count() == 8);
    strategy.restore_script_state();
    baseline();
    // A commit after three deltas adopts a=11 and grid[0,0]=12, then starts a fresh delta budget.
    assert(strategy.mutate(3, 9) == 11);
    strategy.commit_script_state();
    assert(strategy._pf_graph_journal.entry_count() == 0);
    assert(strategy.mutate(3, 7) == 9);
    assert((held.values() == std::vector<int>{9, 5, 7}));
    assert((grid.row(0) == std::vector<int>{10, 2, 2}));
    assert(strategy._pf_graph_journal.entry_count() == 6);
    assert(strategy.mutate(1, 15) == 15);
    assert(strategy._pf_graph_journal.entry_count() == 8);
    strategy.restore_script_state();
    assert((strategy.m.values() == std::vector<int>{11, 5, 7}));
    assert((held.values() == std::vector<int>{11, 5, 7}));
    assert((strategy.grid.row(0) == std::vector<int>{12, 2, 2}));
    assert((grid.row(0) == std::vector<int>{12, 2, 2}));
    assert(strategy._pf_graph_journal.entry_count() == 0);
    std::cout << "ok\n";
}
'''
    # Earlier deltas survive each full preimage and recover the baseline selected by restore or commit.
    assert run_emitted_tu(_with_entry_count(cpp), driver, opt="-O2", label="dense graph writes") == "ok\n"


MATRIX_ROWS = [
    # Set changes just the selected cell, first (1,0) and then (0,1).
    ("set", "int row, int column, int value", "m.set(row, column, value)\nm.get(row, column)",
     "1, 0, 9", 9, 2, 2, [1, 2, 9, 4], "0, 1, 7", 7, 2, 2, [1, 7, 3, 4]),
    # Fill overwrites all four cells with the supplied value.
    ("fill", "int value", "m.fill(value)\nm.get(0, 0)",
     "9", 9, 2, 2, [9, 9, 9, 9], "7", 7, 2, 2, [7, 7, 7, 7]),
    # Inserting a row at 1 splits the originals; at 0 it precedes both originals.
    ("add_row", "int index, int value", "m.add_row(index, array.from(value, value + 1))\nm.rows()",
     "1, 9", 3, 3, 2, [1, 2, 9, 10, 3, 4], "0, 7", 3, 3, 2, [7, 8, 1, 2, 3, 4]),
    # Inserting a column at 1 splits each row; at 0 it prefixes each row.
    ("add_col", "int index, int value", "m.add_col(index, array.from(value, value + 1))\nm.columns()",
     "1, 9", 3, 2, 3, [1, 9, 2, 3, 10, 4], "0, 7", 3, 2, 3, [7, 1, 2, 8, 3, 4]),
    # Removing row 1 keeps [1,2]; removing row 0 keeps [3,4]; the existing return sentinel is zero.
    ("remove_row", "int index", "m.remove_row(index)",
     "1", 0, 1, 2, [1, 2], "0", 0, 1, 2, [3, 4]),
    # Removing column 0 keeps [2,4]; removing column 1 keeps [1,3].
    ("remove_col", "int index", "m.remove_col(index)",
     "0", 0, 2, 1, [2, 4], "1", 0, 2, 1, [1, 3]),
    # Swapping rows exchanges the two pairs; swapping a row with itself does nothing.
    ("swap_rows", "int first, int second", "m.swap_rows(first, second)\nm.get(0, 0)",
     "0, 1", 3, 2, 2, [3, 4, 1, 2], "0, 0", 1, 2, 2, [1, 2, 3, 4]),
    # Swapping columns exchanges each row's two cells; equal indices retain the seed.
    ("swap_columns", "int first, int second", "m.swap_columns(first, second)\nm.get(0, 0)",
     "0, 1", 2, 2, 2, [2, 1, 4, 3], "1, 1", 1, 2, 2, [1, 2, 3, 4]),
    # Generic reshape reads row-major: 1x4 retains four cells and 2x1 truncates the final two.
    ("reshape", "int rows, int columns", "m.reshape(rows, columns)\nm.elements_count()",
     "1, 4", 4, 1, 4, [1, 2, 3, 4], "2, 1", 2, 2, 1, [1, 2]),
    # Constant rows make reversing rows alone or both axes yield the same [3,3,1,1].
    ("reverse", "", "m.reverse()\nm.get(0, 0)",
     "", 3, 2, 2, [3, 3, 1, 1], "", 3, 2, 2, [3, 3, 1, 1]),
    # Descending sort puts the row beginning with 3 first; ascending retains the seed order.
    ("sort", "bool descending", "m.sort(0, descending ? order.descending : order.ascending)\nm.get(0, 0)",
     "true", 3, 2, 2, [3, 4, 1, 2], "false", 1, 2, 2, [1, 2, 3, 4]),
    # Transpose is detached: changing its (0,1) cannot change m; m's own set writes value+1.
    ("transpose", "int value", "t = m.transpose()\nt.set(0, 1, value)\nm.set(1, 0, value + 1)\nt.get(0, 1)",
     "9", 9, 2, 2, [1, 2, 10, 4], "7", 7, 2, 2, [1, 2, 8, 4]),
]


@pytest.mark.parametrize("row", MATRIX_ROWS, ids=[row[0] for row in MATRIX_ROWS])
def test_matrix_mutations_restore_shape_order_and_original_handles(row):
    """Generated matrix mutations restore dimensions and every original cell."""
    name, params, body, call, result, rows, cols, cells, again, result2, rows2, cols2, cells2 = row
    seed = MATRIX_SEED
    seed_cells = [1, 2, 3, 4]  # The ordinary seed writes 1,2 then 3,4 in row order.
    if name == "reverse":
        seed = '''if bar_index == 0
    m.set(0, 0, 1)
    m.set(0, 1, 1)
    m.set(1, 0, 3)
    m.set(1, 1, 3)
'''
        seed_cells = [1, 1, 3, 3]  # Reversing either row's identical cells has no effect.
    cpp = transpile(_source(MATRIX, seed, params, body, call))
    _enrolled(cpp)
    driver = f'''
#include <cassert>
#include <iostream>
template <typename H>
static std::vector<int> cells(const H& matrix) {{
    std::vector<int> values;
    for (int row = 0; row < matrix.rows(); ++row)
        for (int column = 0; column < matrix.columns(); ++column)
            values.push_back(matrix.get(row, column));
    return values;
}}
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    auto independent = strategy.m.copy();
    auto baseline = [&]() {{
        assert(strategy.m.rows() == 2 && strategy.m.columns() == 2);
        assert((cells(strategy.m) == {_vector(seed_cells)}));
        assert(held.rows() == 2 && held.columns() == 2);
        assert((cells(held) == {_vector(seed_cells)}));
        assert((cells(independent) == {_vector(seed_cells)}));
    }};
    baseline();
    strategy.snapshot_script_state();
    assert(strategy.mutate({call}) == {result});
    assert(strategy.m.rows() == {rows} && strategy.m.columns() == {cols});
    assert(held.rows() == {rows} && held.columns() == {cols});
    assert((cells(strategy.m) == {_vector(cells)}));
    assert((cells(held) == {_vector(cells)}));
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.empty());
    strategy.restore_script_state();
    baseline();
    assert(strategy.mutate({again}) == {result2});
    assert(strategy.m.rows() == {rows2} && strategy.m.columns() == {cols2});
    assert((cells(strategy.m) == {_vector(cells2)}));
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.empty());
    std::cout << "ok\\n";
}}
'''
    # Reverse replay restores the literal 2x2 seed after either branch, including after repeated restore.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="matrix " + name) == "ok\n"


def _paired_source():
    return _source(
        "var m = map.new<string, int>()\nvar grid = matrix.new<int>(1, 1, 2)\n", "",
        "string key, int value", "m.put(key, value)\ngrid.set(0, 0, value)\nm.size()", '"a", 7',
    )


def test_commit_next_checkpoint_and_new_run_keep_the_correct_baseline():
    """Both container families share the committed baseline and reset on a new run."""
    cpp = transpile(_paired_source())
    _enrolled(cpp)
    _enrolled(cpp, "grid")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    strategy.mutate("a", 7);
    strategy.commit_script_state();
    assert(strategy._pf_graph_journal.empty());
    strategy.mutate("b", 9);
    strategy.restore_script_state();
    assert((strategy.m.keys() == std::vector<std::string>{"a"}));
    assert((strategy.m.values() == std::vector<int>{7}));
    assert(strategy.grid.get(0, 0) == 7);
    strategy.snapshot_script_state();
    strategy.mutate("c", 11);
    strategy.commit_script_state();
    strategy.mutate("d", 13);
    strategy.restore_script_state();
    strategy.restore_script_state();
    assert((strategy.m.keys() == std::vector<std::string>{"a", "c"}));
    assert((strategy.m.values() == std::vector<int>{7, 11}));
    assert(strategy.grid.get(0, 0) == 11);
    strategy.mutate("e", 17);
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    assert(strategy.m.size() == 0);
    assert(strategy.grid.get(0, 0) == 2);
    assert(strategy._pf_graph_journal.empty());
    std::cout << "ok\n";
}
'''
    # Commits retain a and then c; speculation adds b/d/e, and a fresh run resets both owners.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="map matrix commit and reset") == "ok\n"


def test_initialization_restores_na_bindings_and_first_execution_latches():
    """Restoring a checkpoint before construction permits both roots to initialize again."""
    cpp = transpile(_paired_source())
    _enrolled(cpp)
    _enrolled(cpp, "grid")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    assert(strategy.m.is_na() && strategy.grid.is_na());
    strategy.snapshot_script_state();
    strategy.on_source_bar(bar);
    assert(strategy.m.size() == 0 && strategy.grid.get(0, 0) == 2);
    strategy.mutate("a", 7);
    strategy.restore_script_state();
    assert(strategy.m.is_na() && strategy.grid.is_na());
    assert(!strategy._var_initialized);
    strategy.restore_script_state();
    assert(strategy.m.is_na() && strategy.grid.is_na());
    strategy.on_source_bar(bar);
    assert(strategy.m.size() == 0 && strategy.grid.get(0, 0) == 2);
    strategy.mutate("c", 11);
    strategy.restore_script_state();
    assert(strategy.m.is_na() && strategy.grid.is_na());
    assert(!strategy._var_initialized);
    std::cout << "ok\n";
}
'''
    # Constructor binding records undo after cell/pair records, returning both roots to their original na IDs.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="map matrix first initialization") == "ok\n"


def test_statement_ordered_matrix_initialization_replays_binding_after_payload():
    cpp = transpile(HEADER + '''n = bar_index + 2
var m = matrix.new<int>(n, 1, 0)
m.fill(7)
m.set(0, 0, 9)
''')
    _enrolled(cpp)
    on_bar = cpp.split("    void on_source_bar(const Bar& bar) override {", 1)[1]
    # n is computed before the declaration's one guard and one binding replacement.
    assert on_bar.index("        n = ") < on_bar.index("if (!_pf_var_init_m) {")
    assert on_bar.count("if (!_pf_var_init_m) {") == 1
    assert on_bar.count("this->_pf_graph_journal.replace(m,") == 1
    assert "::take(_pf_var_init_m)" in _snapshot(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
class InitializationProbe : public GeneratedStrategy {
public:
    int visits = 0;
    void on_source_bar(const Bar& bar) override {
        ++visits;
        assert(m.is_na() && !_var_initialized && !_pf_var_init_m);
        snapshot_script_state();
        GeneratedStrategy::on_source_bar(bar);
        // The engine's first bar has index 0: n=2, then fill 7 and set the first cell to 9.
        assert(n == 2 && m.rows() == 2 && m.columns() == 1);
        assert((m.col(0) == std::vector<int>{9, 7}));
        assert(_var_initialized && _pf_var_init_m);
        auto held = m;
        // Initialization saves the na binding before fill saves its one payload preimage.
        assert(_pf_graph_journal.entry_count() == 2);
        restore_script_state();
        assert(m.is_na() && !_var_initialized && !_pf_var_init_m);
        assert(held.rows() == 2 && held.columns() == 1);
        assert((held.col(0) == std::vector<int>{0, 0}));
        assert(_pf_graph_journal.entry_count() == 0);
        restore_script_state();
        assert(m.is_na() && !_var_initialized && !_pf_var_init_m);
        GeneratedStrategy::on_source_bar(bar);
        assert(n == 2 && m.rows() == 2 && m.columns() == 1);
        assert((m.col(0) == std::vector<int>{9, 7}));
        assert(_var_initialized && _pf_var_init_m);
        assert(_pf_graph_journal.entry_count() == 2);
        assert((held.col(0) == std::vector<int>{0, 0}));
        auto second = m;
        restore_script_state();
        // The second initialization owns a new backing; each held backing returns to its zero seed.
        assert(m.is_na() && !_var_initialized && !_pf_var_init_m);
        assert((held.col(0) == std::vector<int>{0, 0}));
        assert((second.col(0) == std::vector<int>{0, 0}));
        assert(_pf_graph_journal.entry_count() == 0);
    }
};
int main() {
    InitializationProbe strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    assert(strategy.visits == 1);  // No orders were placed, so the one bar has one callback.
    std::cout << "ok\n";
}
'''
    # Both executions initialize a 2x1 zero matrix; rollback restores zeros before returning the root to na.
    assert run_emitted_tu(_with_entry_count(cpp), driver, opt="-O2", label="statement ordered matrix initialization") == "ok\n"


def test_every_stateful_helper_instance_records_its_map_mutations():
    cpp = transpile(HEADER + MAP + MAP_SEED + '''step(string key, int value) =>
    var int count = 0
    count += 1
    m.remove(key)
    m.put(key, value + count)
    m.size()
armed = input.bool(false)
if armed
    step("a", 2)
    step("b", 5)
''')
    _enrolled(cpp)
    # Three seed puts plus remove/put in each of the two callable instances yield seven adapters.
    assert cpp.count("this->_pf_graph_journal.map(m)") == 7
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    auto baseline = [&]() {
        assert((strategy.m.keys() == std::vector<std::string>{"a", "b", "c"}));
        assert((strategy.m.values() == std::vector<int>{2, 5, 7}));
        assert((held.keys() == std::vector<std::string>{"a", "b", "c"}));
        assert((held.values() == std::vector<int>{2, 5, 7}));
    };
    strategy.snapshot_script_state();
    strategy.step_cs0("a", 2);
    strategy.step_cs1("b", 5);
    // Each counter first contributes 1; removing and appending a then b leaves c,a,b.
    assert((held.keys() == std::vector<std::string>{"c", "a", "b"}));
    assert((held.values() == std::vector<int>{7, 3, 6}));
    assert(strategy._pf_graph_journal.entry_count() == 1);
    strategy.restore_script_state();
    baseline();
    strategy.step_cs1("b", 9);
    strategy.step_cs0("a", 7);
    // Restore resets each counter to 0; reversing the calls appends b=10 then a=8 after c.
    assert((held.keys() == std::vector<std::string>{"c", "b", "a"}));
    assert((held.values() == std::vector<int>{7, 10, 8}));
    assert(strategy._pf_graph_journal.entry_count() == 1);
    strategy.restore_script_state();
    baseline();
    strategy.step_cs0("a", 2);
    strategy.commit_script_state();
    strategy.step_cs0("a", 2);
    // Commit retains count 1 and a=3; the next call uses count 2 and temporarily writes a=4.
    assert((held.keys() == std::vector<std::string>{"b", "c", "a"}));
    assert((held.values() == std::vector<int>{5, 7, 4}));
    assert(strategy._pf_graph_journal.entry_count() == 1);
    strategy.restore_script_state();
    assert((strategy.m.keys() == std::vector<std::string>{"b", "c", "a"}));
    assert((strategy.m.values() == std::vector<int>{5, 7, 3}));
    assert((held.values() == std::vector<int>{5, 7, 3}));
    assert(strategy._pf_graph_journal.entry_count() == 0);
    std::cout << "ok\n";
}
'''
    # Both helper instances can be the first writer, and each shares the root's single full preimage.
    assert run_emitted_tu(_with_entry_count(cpp), driver, opt="-O2", label="stateful map helper instances") == "ok\n"


def test_defaulted_matrix_additions_and_zero_row_shapes_restore_exactly():
    """Default additions preserve typed na and rollback recovers zero-row shapes."""
    source = _source(
        MATRIX + "var empty = matrix.new<int>(0, 0, 0)\n", MATRIX_SEED,
        "int index, int value, bool erase",
        "m.add_row(index)\nm.add_col(array_id=array.from(value, value, value))\n"
        "empty.add_row(0, array.from(value, value + 1))\n"
        "if erase\n    empty.remove_row(0)\nempty.rows()", "0, 9, true",
    )
    cpp = transpile(source)
    _enrolled(cpp)
    _enrolled(cpp, "empty")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto baseline = [&]() {
        assert(strategy.m.rows() == 2 && strategy.m.columns() == 2);
        assert((strategy.m.row(0) == std::vector<int>{1, 2}));
        assert((strategy.m.row(1) == std::vector<int>{3, 4}));
        assert(!strategy.empty.is_na());
        assert(strategy.empty.rows() == 0 && strategy.empty.columns() == 0);
    };
    baseline();
    strategy.snapshot_script_state();
    assert(strategy.mutate(0, 9, true) == 0);
    assert(strategy.m.rows() == 3 && strategy.m.columns() == 3);
    assert((strategy.m.row(0) == std::vector<int>{-2147483648, -2147483648, 9}));
    assert((strategy.m.row(1) == std::vector<int>{1, 2, 9}));
    assert((strategy.m.row(2) == std::vector<int>{3, 4, 9}));
    assert(strategy.empty.rows() == 0 && strategy.empty.columns() == 0);
    strategy.restore_script_state();
    baseline();
    strategy.restore_script_state();
    assert(strategy.mutate(2, 7, false) == 1);
    assert((strategy.m.row(0) == std::vector<int>{1, 2, 7}));
    assert((strategy.m.row(1) == std::vector<int>{3, 4, 7}));
    assert((strategy.m.row(2) == std::vector<int>{-2147483648, -2147483648, 7}));
    assert(strategy.empty.rows() == 1 && strategy.empty.columns() == 2);
    assert((strategy.empty.row(0) == std::vector<int>{7, 8}));
    strategy.restore_script_state();
    baseline();
    std::cout << "ok\n";
}
'''
    # Index 0 inserts na before the seed; index 2 inserts it after; both empty-matrix paths undo to 0x0.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="matrix default and empty shapes") == "ok\n"


def test_numeric_matrix_shape_preimages_preserve_dimensions_and_cells():
    """Numeric shape preimages restore dimensions and cells through held handles."""
    cpp = transpile(_source(
        "var m = matrix.new<float>(1, 2, 2.0)\n", "", "float value",
        "m.add_row(1, array.from(value, value + 1.0))\nm.remove_col(0)\nm.rows()", "9.0",
    ))
    _enrolled(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    strategy.snapshot_script_state();
    assert(strategy.mutate(9.0) == 2);
    assert(strategy.m.rows() == 2 && strategy.m.columns() == 1);
    assert((strategy.m.col(0) == std::vector<double>{2.0, 10.0}));
    strategy.restore_script_state();
    strategy.restore_script_state();
    assert(held.rows() == 1 && held.columns() == 2);
    assert((held.row(0) == std::vector<double>{2.0, 2.0}));
    strategy.mutate(7.0);
    assert((strategy.m.col(0) == std::vector<double>{2.0, 8.0}));
    strategy.restore_script_state();
    assert(held.rows() == 1 && held.columns() == 2);
    assert((held.row(0) == std::vector<double>{2.0, 2.0}));
    std::cout << "ok\n";
}
'''
    # Add [9,10]/[7,8], then remove column 0; rollback recovers the original 1x2 [2,2].
    assert run_emitted_tu(cpp, driver, opt="-O2", label="numeric matrix shape") == "ok\n"


@pytest.mark.parametrize("count, value, first_row", [
    # Four append/swap/remove cycles replace row 0 with [9+3,9+4], leaving row 1 unchanged.
    (4, 9.0, [12.0, 13.0]),
    # Eight cycles replace row 0 with [7+7,7+8]; the larger loop still retains one full preimage.
    (8, 7.0, [14.0, 15.0]),
])
def test_numeric_structural_loop_retains_one_full_preimage(count, value, first_row):
    cpp = transpile(_source(
        "var m = matrix.new<float>(2, 2, 0.0)\n", MATRIX_SEED, "int count, float value",
        "for index = 0 to count - 1\n"
        "    m.add_row(m.rows(), array.from(value + index, value + index + 1.0))\n"
        "    m.swap_rows(0, m.rows() - 1)\n"
        "    m.remove_row(m.rows() - 1)\nm.rows()", f"{count}, {value}",
    ))
    _enrolled(cpp)
    driver = f'''
#include <cassert>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    auto held = strategy.m;
    strategy.snapshot_script_state();
    assert(strategy.mutate({count}, {value}) == 2);
    assert(strategy.m.rows() == 2 && strategy.m.columns() == 2);
    assert((strategy.m.row(0) == {_vector(first_row, "double")}));
    assert((strategy.m.row(1) == std::vector<double>{{3.0, 4.0}}));
    assert(held.rows() == 2 && held.columns() == 2);
    assert((held.row(0) == {_vector(first_row, "double")}));
    // The first append saves four cells; every later structural edit is covered by that preimage.
    assert(strategy._pf_graph_journal.entry_count() == 1);
    strategy.restore_script_state();
    assert(strategy.m.rows() == 2 && strategy.m.columns() == 2);
    assert((strategy.m.row(0) == std::vector<double>{{1.0, 2.0}}));
    assert((strategy.m.row(1) == std::vector<double>{{3.0, 4.0}}));
    assert(held.rows() == 2 && held.columns() == 2);
    assert((held.row(0) == std::vector<double>{{1.0, 2.0}}));
    assert((held.row(1) == std::vector<double>{{3.0, 4.0}}));
    assert(strategy._pf_graph_journal.entry_count() == 0);
    std::cout << "ok\\n";
}}
'''
    # Each cycle discards the previous first row, while the one preimage restores the literal 2x2 seed.
    assert run_emitted_tu(_with_entry_count(cpp), driver, opt="-O2", label="numeric structural loop") == "ok\n"


def test_generic_axis_deltas_restore_zero_axes_bool_values_and_string_bytes():
    """Lost row and column contents reconstruct shape in the original owners."""
    source = _source(
        'var m = matrix.new<int>(2, 1, 0)\nvar mask = matrix.new<bool>(2, 1, false)\n'
        'var words = matrix.new<string>(1, 2, "")\n', "",
        "int value, bool flag, string word",
        "m.remove_col(0)\nwidth = m.columns()\nm.add_col(0, array.from(value, value + 1))\n"
        "mask.remove_col(0)\nmask.add_col(0, array.from(flag, not flag))\nmask.swap_rows(0, 1)\n"
        'words.remove_row(0)\nwords.add_row(0, array.from(word, word + "!"))\n'
        'words.swap_columns(0, 1)\nwidth', '9, true, "new"',
    )
    cpp = transpile(source)
    for name in ("m", "mask", "words"):
        _enrolled(cpp, name)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.m.set(0, 0, 2);
    strategy.m.set(1, 0, 5);
    strategy.mask.set(0, 0, true);
    strategy.words.set(0, 0, std::string("a\0b", 3));
    strategy.words.set(0, 1, "tail");
    auto held = strategy.words;
    auto baseline = [&]() {
        assert(strategy.m.rows() == 2 && strategy.m.columns() == 1);
        assert((strategy.m.col(0) == std::vector<int>{2, 5}));
        assert(strategy.mask.rows() == 2 && strategy.mask.columns() == 1);
        assert((strategy.mask.col(0) == std::vector<bool>{true, false}));
        assert(held.rows() == 1 && held.columns() == 2);
        assert(held.get(0, 0) == std::string("a\0b", 3));
        assert(held.get(0, 1) == "tail");
    };
    strategy.snapshot_script_state();
    assert(strategy.mutate(9, true, "new") == 0);
    assert((strategy.m.col(0) == std::vector<int>{9, 10}));
    assert((strategy.mask.col(0) == std::vector<bool>{false, true}));
    assert((held.row(0) == std::vector<std::string>{"new!", "new"}));
    strategy.restore_script_state();
    baseline();
    strategy.restore_script_state();
    baseline();
    assert(strategy.mutate(7, false, "again") == 0);
    assert((strategy.m.col(0) == std::vector<int>{7, 8}));
    assert((strategy.mask.col(0) == std::vector<bool>{true, false}));
    assert((held.row(0) == std::vector<std::string>{"again!", "again"}));
    strategy.restore_script_state();
    baseline();
    assert(strategy._pf_graph_journal.empty());
    std::cout << "ok\n";
}
'''
    # Removing the sole column gives width 0; inverse insertions recover [2,5], [true,false] and ['a\0b','tail'].
    assert run_emitted_tu(cpp, driver, opt="-O2", label="generic matrix axis deltas") == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_stored_na_and_matrix_cells_restore_exact_bits(opt):
    """Typed preimages preserve negative zero and a noncanonical NaN payload."""
    cpp = transpile(_source(
        "var m = map.new<string, float>()\nvar grid = matrix.new<float>(1, 2, 0.0)\n", "",
        "float value", 'm.put("zero", value)\nm.put("missing", value + 1.0)\n'
        "grid.set(0, 0, value)\ngrid.fill(value + 2.0)\nm.size()", "9.0",
    ))
    _enrolled(cpp)
    _enrolled(cpp, "grid")
    driver = r'''
#include <cassert>
#include <cstring>
#include <iostream>
static double number(uint64_t bits) { double value; std::memcpy(&value, &bits, 8); return value; }
static uint64_t bits(double value) { uint64_t out; std::memcpy(&out, &value, 8); return out; }
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    const double minus_zero = number(UINT64_C(0x8000000000000000));
    const double missing = number(UINT64_C(0x7ff8000000000042));
    strategy.m.put("zero", minus_zero);
    strategy.m.put("missing", missing);
    strategy.grid.set(0, 0, minus_zero);
    strategy.grid.set(0, 1, missing);
    auto baseline = [&]() {
        assert(strategy.m.size() == 2 && strategy.m.contains("missing"));
        assert((strategy.m.keys() == std::vector<std::string>{"zero", "missing"}));
        assert(bits(strategy.m.get("zero")) == UINT64_C(0x8000000000000000));
        assert(bits(strategy.m.get("missing")) == UINT64_C(0x7ff8000000000042));
        assert(bits(strategy.grid.get(0, 0)) == UINT64_C(0x8000000000000000));
        assert(bits(strategy.grid.get(0, 1)) == UINT64_C(0x7ff8000000000042));
    };
    strategy.snapshot_script_state();
    assert(strategy.mutate(9.0) == 2);
    assert((strategy.m.values() == std::vector<double>{9.0, 10.0}));
    assert((strategy.grid.row(0) == std::vector<double>{11.0, 11.0}));
    strategy.restore_script_state();
    baseline();
    strategy.restore_script_state();
    baseline();
    strategy.mutate(7.0);
    assert((strategy.m.values() == std::vector<double>{7.0, 8.0}));
    assert((strategy.grid.row(0) == std::vector<double>{9.0, 9.0}));
    strategy.restore_script_state();
    baseline();
    std::cout << "ok\n";
}
'''
    # Stored na remains present; undo fill then set recovers both literal floating representations.
    assert run_emitted_tu(cpp, driver, opt=opt, label="map matrix float representations") == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_bool_string_and_color_preimages_keep_their_actual_types(opt):
    """False values, embedded NUL bytes and wide color storage survive rollback."""
    cpp = transpile(_source(
        'var m = map.new<string, string>()\nvar flags = map.new<string, bool>()\n'
        'var words = matrix.new<string>(1, 2, "")\nvar mask = matrix.new<bool>(1, 1, false)\n'
        'var colors = matrix.new<color>(1, 1, color.red)\n', "",
        "string key, string value, bool flag, color paint",
        'm.put(key, value)\nflags.put("flag", flag)\nwords.fill(value)\n'
        'mask.set(0, 0, flag)\ncolors.set(0, 0, paint)\nm.size()', '"key", "new", true, color.blue',
    ))
    for name in ("m", "flags", "words", "mask", "colors"):
        _enrolled(cpp, name)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.m.put(std::string("k\0y", 3), std::string("a\0b", 3));
    strategy.flags.put("flag", false);
    strategy.words.set(0, 0, std::string("a\0b", 3));
    strategy.words.set(0, 1, "tail");
    strategy.colors.set(0, 0, INT64_C(0x11223344));
    auto baseline = [&]() {
        assert((strategy.m.keys() == std::vector<std::string>{std::string("k\0y", 3)}));
        assert(strategy.m.get(std::string("k\0y", 3)) == std::string("a\0b", 3));
        assert(strategy.flags.contains("flag") && !strategy.flags.get("flag"));
        assert(strategy.words.get(0, 0) == std::string("a\0b", 3));
        assert(strategy.words.get(0, 1) == "tail");
        assert(!strategy.mask.get(0, 0));
        assert(strategy.colors.get(0, 0) == INT64_C(0x11223344));
    };
    strategy.snapshot_script_state();
    assert(strategy.mutate(std::string("k\0y", 3), std::string("n\0w", 3), true, INT64_C(0x55667788)) == 1);
    assert(strategy.m.get(std::string("k\0y", 3)) == std::string("n\0w", 3));
    assert(strategy.flags.get("flag"));
    assert(strategy.words.get(0, 0) == std::string("n\0w", 3));
    assert(strategy.words.get(0, 1) == std::string("n\0w", 3));
    assert(strategy.mask.get(0, 0));
    assert(strategy.colors.get(0, 0) == INT64_C(0x55667788));
    strategy.restore_script_state();
    baseline();
    strategy.restore_script_state();
    strategy.mutate(std::string("k\0y", 3), "again", false, INT64_C(0x76543210));
    assert(strategy.m.get(std::string("k\0y", 3)) == "again");
    assert(strategy.words.get(0, 1) == "again");
    assert(!strategy.flags.get("flag") && !strategy.mask.get(0, 0));
    assert(strategy.colors.get(0, 0) == INT64_C(0x76543210));
    strategy.restore_script_state();
    baseline();
    std::cout << "ok\n";
}
'''
    # Each branch changes the actual typed inputs, then restores false, 'a\0b'/'tail' and 0x11223344.
    assert run_emitted_tu(cpp, driver, opt=opt, label="typed graph preimages") == "ok\n"


def test_argument_effects_precede_matrix_validation_and_propagate_run_stops():
    """Errors retain the original method and earlier argument mutations."""
    cpp = transpile(_source(
        "var m = map.new<string, int>()\nvar grid = matrix.new<int>(1, 1, 2)\n", "",
        "int row, int value, bool stop",
        "grid.set(row, 0, operand(value, stop))\ngrid.get(0, 0)", "0, 7, false",
        '''operand(int value, bool stop) =>
    m.put(str.tostring(value), value + 1)
    if stop
        runtime.error("operand stopped")
    value
''',
    ))
    _enrolled(cpp)
    _enrolled(cpp, "grid")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    try { strategy.mutate(1, 7, false); assert(false); }
    catch (const std::exception& error) {
        assert(std::string(error.what()) == "matrix.set: row index out of range");
    }
    assert((strategy.m.keys() == std::vector<std::string>{"7"}));
    assert((strategy.m.values() == std::vector<int>{8}));
    assert(strategy.grid.get(0, 0) == 2);
    strategy.restore_script_state();
    assert(strategy.m.size() == 0);
    try { strategy.mutate(0, 9, true); assert(false); }
    catch (const std::exception& error) {
        assert(std::string(error.what()) == "operand stopped");
    }
    assert((strategy.m.keys() == std::vector<std::string>{"9"}));
    assert((strategy.m.values() == std::vector<int>{10}));
    assert(strategy.grid.get(0, 0) == 2);
    strategy.restore_script_state();
    assert(strategy.m.size() == 0);
    assert(strategy.mutate(0, 11, false) == 11);
    assert((strategy.m.values() == std::vector<int>{12}));
    strategy.restore_script_state();
    assert(strategy.m.size() == 0 && strategy.grid.get(0, 0) == 2);
    assert(strategy._pf_graph_journal.empty());
    std::cout << "ok\n";
}
'''
    # The operand writes 7->8 or 9->10 before failing; a valid call writes 11->12 and cell 11.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="graph operand order") == "ok\n"


@pytest.mark.parametrize("declaration, body, message", [
    # A typed na map fails its original put before creating any undo record.
    ('var map<string, int> m = na\n', 'm.put("a", value)', "map operation on na ID"),
    # A typed na matrix fails its original set, never an early get.
    ('var matrix<int> m = na\n', 'm.set(0, 0, value)\nvalue', "matrix operation on na ID"),
])
def test_na_owner_mutation_retains_the_original_error(declaration, body, message):
    """A null owner has no payload and remains null after restore."""
    cpp = transpile(_source(declaration, "", "int value", body, "9"))
    _enrolled(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    try { strategy.mutate(9); assert(false); }
    catch (const std::exception& error) { assert(std::string(error.what()) == MESSAGE); }
    assert(strategy.m.is_na());
    assert(strategy._pf_graph_journal.empty());
    strategy.restore_script_state();
    strategy.restore_script_state();
    assert(strategy.m.is_na());
    std::cout << "ok\n";
}
'''.replace("MESSAGE", json.dumps(message))
    # No mutation occurred, so the journal is empty and the owner is still na.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="na graph owner") == "ok\n"


def test_same_matrix_argument_shape_change_precedes_set_capture():
    """A nested removal changes the target shape before the outer setter checks it."""
    source = _source(
        MATRIX, MATRIX_SEED, "int row, int value",
        "m.set(row, 0, shrink(value))\nm.get(0, 0)", "0, 9",
        "shrink(int value) =>\n    m.remove_row(1)\n    value\n",
    )
    cpp = transpile(source)
    _enrolled(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    assert(strategy.mutate(0, 9) == 9);
    assert(strategy.m.rows() == 1 && strategy.m.columns() == 2);
    assert((strategy.m.row(0) == std::vector<int>{9, 2}));
    strategy.restore_script_state();
    assert(strategy.m.rows() == 2 && strategy.m.columns() == 2);
    assert((strategy.m.row(0) == std::vector<int>{1, 2}));
    assert((strategy.m.row(1) == std::vector<int>{3, 4}));
    strategy.restore_script_state();
    try { strategy.mutate(1, 7); assert(false); }
    catch (const std::exception& error) {
        assert(std::string(error.what()) == "matrix.set: row index out of range");
    }
    assert(strategy.m.rows() == 1 && strategy.m.columns() == 2);
    assert((strategy.m.row(0) == std::vector<int>{1, 2}));
    strategy.restore_script_state();
    assert(strategy.m.rows() == 2 && strategy.m.columns() == 2);
    assert((strategy.m.row(0) == std::vector<int>{1, 2}));
    assert((strategy.m.row(1) == std::vector<int>{3, 4}));
    assert(strategy._pf_graph_journal.empty());
    std::cout << "ok\n";
}
'''
    # The valid call leaves [9,2]; row 1 becomes invalid after removal and leaves [1,2] until restore.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="matrix operand shape order") == "ok\n"


ALLOCATION_OBSERVER = r'''
#include <cassert>
#include <iostream>
#include <new>
static bool observe_allocations = false;
static size_t observed_bytes = 0;
void* operator new(std::size_t size) {
    if (observe_allocations) observed_bytes += size;
    if (void* value = std::malloc(size ? size : 1)) return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }
'''


@pytest.mark.parametrize("declaration, seed, body", [
    # A warmed three-pair map used to allocate ordered-entry and index copies at both hooks.
    (MAP, MAP_SEED, 'm.put("d", value)\nm.size()'),
    # A warmed 2x2 generic matrix used to allocate its outer snapshot and copied cells at both hooks.
    (MATRIX, MATRIX_SEED, 'm.set(0, 0, value)\nm.get(0, 0)'),
])
def test_warmed_snapshot_and_commit_allocate_no_graph_payload(declaration, seed, body):
    """Warmed checkpoint hooks allocate no map or matrix payload storage."""
    cpp = transpile(_source(declaration, seed, "int value", body, "9"))
    driver = ALLOCATION_OBSERVER + r'''
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    observed_bytes = 0;
    observe_allocations = true;
    strategy.snapshot_script_state();
    observe_allocations = false;
    const size_t snapshot_bytes = observed_bytes;
    strategy.mutate(9);
    observed_bytes = 0;
    observe_allocations = true;
    strategy.commit_script_state();
    observe_allocations = false;
    std::cout << snapshot_bytes << " " << observed_bytes << "\n";
}
'''
    # Tokens allocate zero bytes at either hook; the actual mutation lies outside the measured interval.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="graph checkpoint allocations") == "0 0\n"
    _enrolled(cpp)


@pytest.mark.parametrize("inputs, keys, values", [
    # Four snapshot/commit cycles formerly copied 0+1+2+3+1+2+3+4 = 16 pairs.
    ([2, 5, 7, 11], ["2", "5", "7", "11"], [3, 6, 8, 12]),
    # Eight cycles formerly copied 28+36 = 64 pairs; each value is its actual inserted key plus one.
    ([2, 5, 7, 11, 13, 17, 19, 23], ["2", "5", "7", "11", "13", "17", "19", "23"], [3, 6, 8, 12, 14, 18, 20, 24]),
])
def test_growing_map_has_no_checkpoint_payload_allocations(inputs, keys, values):
    """Map growth changes real contents while checkpoint hook allocations remain zero."""
    cpp = transpile(_source(
        "var m = map.new<string, int>()\n", "", "int value",
        "m.put(str.tostring(value), value + 1)\nm.size()", "2",
    ))
    driver = ALLOCATION_OBSERVER + r'''
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    observed_bytes = 0;
    for (int key : INPUTS) {
        observe_allocations = true;
        strategy.snapshot_script_state();
        observe_allocations = false;
        strategy.mutate(key);
        observe_allocations = true;
        strategy.commit_script_state();
        observe_allocations = false;
    }
    assert((strategy.m.keys() == KEYS));
    assert((strategy.m.values() == VALUES));
    strategy.restore_script_state();
    assert((strategy.m.keys() == KEYS));
    assert((strategy.m.values() == VALUES));
    std::cout << observed_bytes << "\n";
}
'''.replace("INPUTS", _vector(inputs)).replace("KEYS", _vector(keys, "std::string")).replace("VALUES", _vector(values))
    # Neither four nor eight generations copy any map payload at snapshot or commit.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="growing map allocations") == "0\n"
    _enrolled(cpp)


GRAPH_TRACE_NAMES = (
    "map_size", "map_last", "k0", "k1", "k2", "k3", "k4", "k5", "k6", "k7",
    "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
)


def _scheduled_source(units, eager=False):
    source = schedule._source(units).replace("callbacks += 1\n", '''callbacks += 1
var m = map.new<string, int>()
m.put(str.tostring(chartWalks), chartWalks * 10)
''')
    if eager:
        # A harmless value argument makes the root escape the direct-use proof.
        source += '''observe(map<string, int> value) => value.size()
observed = observe(m)
'''
    source += '''// @pf-trace map_size=m.size()
// @pf-trace map_last=m.get(str.tostring(chartWalks))
'''
    for prefix, operation in (("k", "keys"), ("v", "values")):
        for index in range(8):
            value = f"m.{operation}().get({index})"
            if operation == "keys":
                value = f"str.tonumber({value})"
            source += f"// @pf-trace {prefix}{index}=m.size() > {index} ? {value} : -1\n"
    return source


def _scheduled_run(engine, directory, feed, aux, key, units, eager=False):
    work = directory / key
    work.mkdir()
    (work / "inputs.json").write_text(json.dumps({
        "input_tf": "15", "script_tf": "15",
        "aux_security_ohlcv_csv": str(aux), "aux_security_input_tf": "1",
        "runtime_overrides": {
            "mintick": schedule.TICK, "pointvalue": 1, "qty_step": 1,
            "currency": "USD", "timezone": "UTC", "syminfo_metadata": {"mincontract": 1},
        },
    }), encoding="utf-8")
    return ok(execute_all(engine, feed, directory, {
        key: Build(_scheduled_source(units, eager), trace=True),
    }), key)


def _scheduled_frames(records, chart):
    names = schedule.TRACE_NAMES + GRAPH_TRACE_NAMES
    assert records and len(records) % len(names) == 0
    frames = {}
    for offset in range(0, len(records), len(names)):
        chunk = records[offset:offset + len(names)]
        assert tuple(record["name"] for record in chunk) == names
        timestamp = chunk[0]["timestamp"]
        assert all(record["timestamp"] == timestamp for record in chunk)
        frames.setdefault(timestamp, []).append({record["name"]: record["value"] for record in chunk})
    assert set(frames) == {row[0] for row in chart}
    return frames


SCHEDULED_MAPS = [
    # One committed step inserts key 1 with value 10; unused slots are -1.
    (1, 10, [1, -1, -1, -1, -1, -1, -1, -1], [10, -1, -1, -1, -1, -1, -1, -1]),
    # The second committed step appends 2->20 after 1->10.
    (2, 20, [1, 2, -1, -1, -1, -1, -1, -1], [10, 20, -1, -1, -1, -1, -1, -1]),
    # The third committed step appends 3->30.
    (3, 30, [1, 2, 3, -1, -1, -1, -1, -1], [10, 20, 30, -1, -1, -1, -1, -1]),
    # The fourth committed step appends 4->40 before the late margin callback.
    (4, 40, [1, 2, 3, 4, -1, -1, -1, -1], [10, 20, 30, 40, -1, -1, -1, -1]),
    # The late callback speculatively appends 5->50; bar 4 repeats it after restore.
    (5, 50, [1, 2, 3, 4, 5, -1, -1, -1], [10, 20, 30, 40, 50, -1, -1, -1]),
    # The sixth committed step appends 6->60.
    (6, 60, [1, 2, 3, 4, 5, 6, -1, -1], [10, 20, 30, 40, 50, 60, -1, -1]),
    # The seventh committed step appends 7->70.
    (7, 70, [1, 2, 3, 4, 5, 6, 7, -1], [10, 20, 30, 40, 50, 60, 70, -1]),
    # The eighth committed step appends 8->80, leaving eight ordered pairs.
    (8, 80, [1, 2, 3, 4, 5, 6, 7, 8], [10, 20, 30, 40, 50, 60, 70, 80]),
]


def _assert_map_frame(row, expected):
    assert (row["map_size"], row["map_last"]) == expected[:2]
    assert [row[f"k{index}"] for index in range(8)] == expected[2]
    assert [row[f"v{index}"] for index in range(8)] == expected[3]


def test_growing_map_matches_eager_contents_and_trades_under_fill_callbacks(tmp_path):
    """Fill callbacks preserve map contents and trades relative to eager checkpoints."""
    _enrolled(transpile(_scheduled_source(2)))
    assert "_pf_graph_journal" not in transpile(_scheduled_source(2, eager=True))
    engine = skip_unless_e2e_env()
    feed, aux, chart, _minutes = schedule._feeds(tmp_path)
    control = _scheduled_run(engine, tmp_path, feed, aux, "control", 2)
    control_rows = _scheduled_frames(control.traces["default"], chart)
    # 30m advances every two 15m bars; the first 5m bucket advances by three per chart step.
    higher = [0, 1, 1, 2, 2, 3, 3, 4]
    finer = [1, 4, 7, 10, 13, 16, 19, 22]
    # Two funded units never trigger a margin callback; chart state advances once per committed bar.
    positions = [0, 2, 2, 2, 2, 2, 2, 2]
    walks = [1, 2, 3, 4, 5, 6, 7, 8]
    for index, bar in enumerate(chart):
        for row in control_rows[bar[0]]:
            _assert_map_frame(row, SCHEDULED_MAPS[index])
            assert (row["higher"], row["finer"]) == (higher[index], finer[index])
            assert row["chart_walks"] == walks[index]
            assert row["position"] == positions[index]
            assert row["closed"] == row["margin"] == row["margin_price"] == 0
    assert len(control_rows[chart[3][0]]) == 1  # No fill occurs on control bar 3.

    eager = _scheduled_run(engine, tmp_path, feed, aux, "eager", 3, eager=True)
    journal = _scheduled_run(engine, tmp_path, feed, aux, "journal", 3)
    assert journal.trades == eager.trades
    assert journal.traces == eager.traces
    frames = _scheduled_frames(journal.traces["default"], chart)
    late = frames[chart[3][0]]
    # The full-margin event closes one of three units after the ordinary fourth mutation.
    assert [(row["position"], row["closed"], row["margin"]) for row in late] == [(3, 0, 0), (2, 1, 1)]
    assert [row["chart_walks"] for row in late] == [4, 5]
    assert late[1]["margin_price"] == 100_000_000.25
    assert late[1]["callbacks"] - late[0]["callbacks"] == 1
    assert frames[chart[4][0]][0]["callbacks"] - late[1]["callbacks"] == 1
    assert sum(map(len, frames.values())) - sum(map(len, control_rows.values())) == 1
    # The margin trade persists, while the provisional map insertion is rolled back before bar 4.
    positions = [0, 3, 3, 3, 2, 2, 2, 2]
    closed = [0, 0, 0, 0, 1, 1, 1, 1]
    for index, bar in enumerate(chart):
        for execution, row in enumerate(frames[bar[0]]):
            is_late = index == 3 and execution == 1
            _assert_map_frame(row, SCHEDULED_MAPS[4] if is_late else SCHEDULED_MAPS[index])
            assert (row["higher"], row["finer"]) == (higher[index], finer[index])
            assert row["position"] == (2 if is_late else positions[index])
            assert row["closed"] == row["margin"] == (1 if is_late else closed[index])
