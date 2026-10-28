"""Frozen array versions and exact rollback; native and E2E rows are LAB-ONLY."""

from __future__ import annotations

import json

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import base, tables
from pineforge_codegen.codegen.base import CodeGen
from pineforge_codegen.collection_history import ARRAY_CHANGING_METHODS
from tests._compile import run_emitted_tu
from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests import test_e2e_security_coof_restore_witness as schedule


HEADER = '//@version=6\nstrategy("frozen arrays")\n'
COOF_HEADER = '//@version=6\nstrategy("frozen arrays", calc_on_order_fills=true)\n'
SEED = "var a = array.from(2, 5)\n"
HISTORY = "older = na(a[3]) ? -1 : (a[3]).size()\n"
OLD_MEMBER = "    _PFCollectionHistory<decltype(a)> _pf_collection_hist_a{4};"
SHARED_MEMBER = "    _PFCachedCollectionHistory<decltype(a)> _pf_collection_hist_a{4};"
WRITER = "this->_pf_collection_hist_a.writer(this->_pf_history_versions)"


def _shared(cpp, *, capacity=4):
    # One proven declaration selects one cached history with its existing capacity.
    suffix = "" if capacity is None else "{" + str(capacity) + "}"
    assert SHARED_MEMBER.replace("{4}", suffix) in cpp
    assert "class _PFHistoryVersionClock" in cpp
    assert WRITER + ".replace(a," in cpp


def _eager_cpp(source, monkeypatch):
    def eager(self):
        self._history_sharing = None

    with monkeypatch.context() as patch:
        patch.setattr(CodeGen, "_prepare_history_sharing", eager, raising=False)
        return transpile(source)


@pytest.mark.parametrize("header", [HEADER, COOF_HEADER])
def test_history_sharing_does_not_require_a_fill_checkpoint(header):
    cpp = transpile(header + SEED + HISTORY)
    _shared(cpp)
    snapshot = cpp.split("    void snapshot_script_state() override {", 1)[1].split(
        "    void restore_script_state() override {", 1)[0]
    # Payload, ring, cache and logical token are copied; the allocator is excluded.
    assert "            a,\n" in snapshot
    assert "            _pf_collection_hist_a,\n" in snapshot
    assert "            _pf_history_versions,\n" not in snapshot
    assert "this->_pf_history_versions.reset_for_run();" in cpp
    assert "this->_pf_collection_hist_a = decltype(this->_pf_collection_hist_a){4};" in cpp


@pytest.mark.parametrize("operation, fragment", [
    # Both spellings of push dirty the live owner at its append boundary.
    ("a.push(7)", ".push(a, 7)"),
    ("array.push(a, 7)", ".push(a, 7)"),
    # Set dirties only after its checked index and value have evaluated.
    ("a.set(1, 9)", ".save_set(__pf_array, (size_t)__pf_array_index)"),
    ("array.set(value=9, id=a, index=-1)", ".save_set(__pf_array, (size_t)__pf_array_index)"),
    # Removal checks precede the old element's removal and version change.
    ("a.pop()", ".save_remove(__pf_array, __pf_array.size()-1)"),
    ("a.shift()", ".save_remove(__pf_array, 0)"),
    ("a.remove(1)", ".save_remove(__pf_array, (size_t)__pf_array_index)"),
    # Each insertion reaches the shared insertion adapter.
    ("a.insert(1, 7)", ".insert(__pf_array,"),
    ("a.unshift(7)", ".insert(a, a.begin(), 7)"),
    # Bulk writes also invalidate versions even when size is unchanged.
    ("a.clear()", ".clear(a)"),
    ("a.fill(7)", ".fill(a, a.begin(), a.end(), 7)"),
    ("a.fill(7, index_to=1)", ".fill(__pf_array,"),
    ("a.sort()", ".sort(a, a.begin(), a.end())"),
    ("a.reverse()", ".reverse(a, a.begin(), a.end())"),
    # Legacy current-element assignments retain their checked lvalue.
    ("a[1] := 9", ".save_set(_pf_history_array, (size_t)_pf_history_offset)"),
    ("a[1] += 9", ".save_set(_pf_history_array, (size_t)_pf_history_offset)"),
    # The literal-zero receiver still lowers to the live array and must be tracked.
    ("(a[0]).push(7)", ".push(a, 7)"),
])
def test_every_mutating_family_uses_the_observed_boundary(operation, fragment):
    cpp = transpile(HEADER + SEED + operation + "\n" + HISTORY)
    _shared(cpp)
    assert cpp.count(WRITER + fragment) == 1  # One authored operation has one emitted dirty site.


FALLBACKS = [
    # A get used as an lvalue bypasses the checked mutator catalogue.
    (HEADER + SEED + "a.get(0) := 9\n" + HISTORY, OLD_MEMBER),
    # A live bare alias escapes the owner proof.
    (HEADER + SEED + "alias = a\nalias.push(7)\n" + HISTORY, OLD_MEMBER),
    # A literal-zero history argument follows the existing live-vector shortcut.
    (HEADER + SEED + "observe(array<int> xs) => xs.size()\nr = observe(a[0])\n" + HISTORY, OLD_MEMBER),
    # A transitive requested read places the owner outside the chart-only domain.
    (HEADER + SEED + 'readSize() => a.size()\nr = request.security(syminfo.tickerid, "60", readSize())\n'
     + HISTORY, OLD_MEMBER),
    # A requested global also reaches the owner through its definition.
    (HEADER + SEED + 's = a.size()\nr = request.security(syminfo.tickerid, "60", s)\n'
     + HISTORY, OLD_MEMBER),
    # A map keeps the existing handle-aware policy; map history itself is unsupported.
    (HEADER + SEED + "var m = map.new<string, int>()\nm.put(\"x\", 1)\n" + HISTORY, OLD_MEMBER),
    # Matrix payloads are outside primitive-array ownership.
    (HEADER + "var a = matrix.new<int>(1, 1, 2)\nr = na(a[3]) ? -1 : (a[3]).rows()\n", OLD_MEMBER),
    # Object-bearing arrays retain the old shallow/deep snapshot boundary.
    (HEADER + "type Item\n    int value\nvar a = array.from(Item.new(2))\n" + HISTORY, OLD_MEMBER),
    # No past read means no collection-history object or version allocator.
    (HEADER + SEED + "a.push(7)\n", "    std::vector<int> a;"),
    # Varip has a different rollback domain and remains unconditional.
    (HEADER + "varip a = array.from(2, 5)\n" + HISTORY, OLD_MEMBER),
    # Ordinary declarations replace their value every bar and are not stable var owners.
    (HEADER + "a = array.from(2, 5)\n" + HISTORY, OLD_MEMBER),
    # For-in retains its current-array shortcut and is conservatively excluded.
    (HEADER + SEED + "int sum = 0\nfor value in a[1]\n    sum += value\n" + HISTORY, OLD_MEMBER),
    # Self-concat has no proved iterator lifetime.
    (HEADER + SEED + "a.concat(a)\n" + HISTORY, OLD_MEMBER),
    # A same-spelled parameter makes direct global resolution uncertain.
    (HEADER + SEED + "sizeOf(array<int> a) => a.size()\nr = sizeOf(array.from(7))\n" + HISTORY, OLD_MEMBER),
    # An authored method can dispatch independently of the built-in catalogue.
    (HEADER + "method push(array<int> xs, int value) => xs.size()\n" + SEED
     + "a.push(7)\n" + HISTORY, OLD_MEMBER),
]


@pytest.mark.parametrize("source, old_fragment", FALLBACKS)
def test_eligible_twin_changes_while_unproved_owners_keep_all_bytes(source, old_fragment, monkeypatch):
    # The positive twin is the old-code red; each exclusion is a preservation control.
    _shared(transpile(HEADER + SEED + HISTORY))
    cpp = transpile(source)
    assert old_fragment in cpp
    assert "class _PFCachedCollectionHistory" not in cpp
    assert cpp == _eager_cpp(source, monkeypatch)


def test_one_uncovered_mutator_disables_the_entire_owner(monkeypatch):
    source = HEADER + SEED + "a.push(7)\na.set(0, 9)\n" + HISTORY
    _shared(transpile(source))
    # Removing set from coverage must also turn off the otherwise covered push.
    monkeypatch.setattr(base, "ARRAY_JOURNAL_DELTA_METHODS", base.ARRAY_JOURNAL_DELTA_METHODS - {"set"})
    cpp = transpile(source)
    assert OLD_MEMBER in cpp
    assert cpp == _eager_cpp(source, monkeypatch)
    assert tables.ARRAY_JOURNAL_DELTA_METHODS | tables.ARRAY_JOURNAL_FULL_METHODS == ARRAY_CHANGING_METHODS


def test_generated_history_support_names_avoid_authored_bindings():
    cpp = transpile(HEADER + '''int _PFCachedCollectionHistory = 2
int _PFHistoryVersionClock = 5
int _pf_history_versions = 7
''' + SEED + HISTORY)
    # Each occupied name receives the first available suffix independently.
    assert "class _PFCachedCollectionHistory__pf2" in cpp
    assert "class _PFHistoryVersionClock__pf2" in cpp
    assert "_PFHistoryVersionClock__pf2 _pf_history_versions__pf2;" in cpp
    assert "this->_pf_collection_hist_a.writer(this->_pf_history_versions__pf2).replace(a," in cpp


def test_traced_writes_stay_after_the_freeze_and_mark_the_next_version():
    cpp = transpile(HEADER + SEED + HISTORY + "// @pf-trace removed=bar_index == 1 ? a.pop() : a.size()\n")
    _shared(cpp)
    # The trace's pop happens after close and marks the live value for the following close.
    close = cpp.index("_pf_collection_hist_a.close(a, this->_pf_history_versions);")
    mutation = cpp.index(WRITER + ".save_remove(__pf_array, __pf_array.size()-1)")
    assert mutation > close


def _count_freezes(cpp):
    """Count the actual detached trait allocation in both old and cached output."""
    old = "return std::make_shared<const std::vector<E, A>>(value);"
    assert cpp.count(old) == 1  # There is one vector freeze definition, shared by every history.
    counted = (
        "auto frozen = std::make_shared<const std::vector<E, A>>(value); "
        "++pf_test_freezes; pf_test_copied_elements += value.size(); return frozen;"
    )
    return ("static unsigned long long pf_test_freezes = 0;\n"
            "static unsigned long long pf_test_copied_elements = 0;\n" + cpp.replace(old, counted))


DRIVER_SUPPORT = r'''
#include <cassert>
#include <cstring>
#include <iostream>
struct ClockedStrategy : GeneratedStrategy {
    void set_test_timestamp(int64_t timestamp) { current_bar_.timestamp = timestamp; }
};
static std::vector<Bar> bars(int count) {
    std::vector<Bar> result;
    for (int i = 0; i < count; ++i)
        result.push_back(Bar{1.0, 1.0, 1.0, 1.0, 1.0, i * int64_t{60000}});
    return result;
}
template <typename S>
auto open_history(S& s, bool advance, int) -> decltype(
    s._pf_collection_hist_a.open(s.a, advance, s._pf_history_versions), void()) {
    s._pf_collection_hist_a.open(s.a, advance, s._pf_history_versions);
}
template <typename S>
void open_history(S& s, bool advance, long) {
    s._pf_collection_hist_a.open(s.a, advance);
}
template <typename S>
auto close_history(S& s, int) -> decltype(
    s._pf_collection_hist_a.close(s.a, s._pf_history_versions), void()) {
    s._pf_collection_hist_a.close(s.a, s._pf_history_versions);
}
template <typename S>
void close_history(S& s, long) {
    s._pf_collection_hist_a.close(s.a);
}
'''


def _native(cpp, body, *, opt="-O2"):
    # LAB-ONLY: the same native helper links the full emitted TU to the paired runtime.
    driver = DRIVER_SUPPORT + "\nint main() {\n" + body + '\nstd::cout << "ok\\n";\n}\n'
    assert run_emitted_tu(_count_freezes(cpp), driver, opt=opt, label="frozen array versions") == "ok\n"


def test_unchanged_history_shares_one_detached_payload_over_many_bars():
    cpp = transpile(HEADER + SEED + '''var seen = array.new_int()
seen.push(na(a[1]) ? -1 : (a[1]).get(1))
''' + HISTORY)
    # Eight closes keep [2,5]; only the first bar lacks a previous value.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(8);
    strategy.run(feed.data(), 8);
    assert(strategy.last_error().empty());
    assert((strategy.seen == std::vector<int>{-1, 5, 5, 5, 5, 5, 5, 5}));
    assert(pf_test_freezes == 1);
    assert(pf_test_copied_elements == 2);
    auto& h = strategy._pf_collection_hist_a;
    assert((h.at(1) == std::vector<int>{2, 5}));
    assert((h.at(2) == std::vector<int>{2, 5}));
    assert((h.at(3) == std::vector<int>{2, 5}));
    assert(&h.at(1) == &h.at(2));
    assert(&h.at(2) == &h.at(3));
    assert(&h.at(1) != &strategy.a);
    assert(h.is_na(4));
''')


@pytest.mark.parametrize("write, seen, first, previous, copied", [
    # Appending 7 on bar 2 freezes [2,5] once and [2,5,7] once: 2+3 elements.
    ("a.push(7)", "{-1, 2, 2, 3, 3}", "{2, 5, 7}", "{2, 5}", 5),
    # Set changes a value without changing length: 2+2 elements.
    ("a.set(1, 9)", "{-1, 2, 2, 2, 2}", "{2, 9}", "{2, 5}", 4),
    # Restoring 5 still writes twice; identical contents belong to distinct versions.
    ("a.set(1, 9)\n    a.set(1, 5)", "{-1, 2, 2, 2, 2}", "{2, 5}", "{2, 5}", 4),
])
def test_writes_make_a_new_frozen_version_even_when_contents_return(write, seen, first, previous, copied):
    cpp = transpile(HEADER + SEED + "if bar_index == 2\n    " + write + "\n"
                    + "var seen = array.new_int()\nseen.push(na(a[1]) ? -1 : (a[1]).size())\n" + HISTORY)
    _native(cpp, f'''
    GeneratedStrategy strategy;
    auto feed = bars(5);
    strategy.run(feed.data(), 5);
    assert(strategy.last_error().empty());
    assert((strategy.seen == std::vector<int>{seen}));
    assert(pf_test_freezes == 2);
    assert(pf_test_copied_elements == {copied});
    auto& h = strategy._pf_collection_hist_a;
    assert((h.at(1) == std::vector<int>{first}));
    assert((h.at(2) == std::vector<int>{first}));
    assert((h.at(3) == std::vector<int>{previous}));
    assert(&h.at(1) == &h.at(2));
    assert(&h.at(2) != &h.at(3));
''')


def test_empty_frozen_array_is_present_while_unfilled_history_is_na():
    cpp = transpile(HEADER + '''var a = array.new_int()
var seen = array.new_int()
seen.push(na(a[1]) ? -1 : (a[1]).size())
''' + HISTORY)
    # One empty snapshot copies zero elements; missing history is only the first observation.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(5);
    strategy.run(feed.data(), 5);
    assert(strategy.last_error().empty());
    assert((strategy.seen == std::vector<int>{-1, 0, 0, 0, 0}));
    assert(pf_test_freezes == 1);
    assert(pf_test_copied_elements == 0);
    auto& h = strategy._pf_collection_hist_a;
    assert(!h.is_na(1));
    assert(h.at(1).empty());
    assert(&h.at(1) == &h.at(2));
    assert(h.is_na(4));
    assert(!h.reference(1).is_na());
    assert(h.reference(4).is_na());
    bool stopped = false;
    try { (void)h.at(4); }
    catch (const std::exception& error) {
        stopped = true;
        assert(std::string(error.what()) == "Cannot call array methods when id of array is na.");
    }
    assert(stopped);
''')


def test_copy_and_reference_values_remain_detached_from_shared_slots():
    cpp = transpile(HEADER + SEED + '''var int copied = 0
if bar_index == 2
    detached = array.copy(a[1])
    detached.set(0, 9)
    copied := detached.get(0)
''' + HISTORY)
    # The explicit copy holds [9,5]; the wrapper is another independent [2,5] copy.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(5);
    strategy.run(feed.data(), 5);
    assert(strategy.last_error().empty());
    assert(strategy.copied == 9);
    assert(pf_test_freezes == 1);
    assert(pf_test_copied_elements == 2);
    auto& h = strategy._pf_collection_hist_a;
    auto held = h.reference(1);
    held.get().at(0) = 11;
    auto detached = h.value(1);
    detached.at(1) = 13;
    for (int i = 0; i < 8; ++i) {
        open_history(strategy, true, 0);
        close_history(strategy, 0);
    }
    assert((held.get() == std::vector<int>{11, 5}));
    assert((detached == std::vector<int>{2, 13}));
    assert((h.at(1) == std::vector<int>{2, 5}));
    assert((strategy.a == std::vector<int>{2, 5}));
    assert(pf_test_freezes == 1);
    assert(&h.at(1) == &h.at(2));
''')


@pytest.mark.parametrize("count, copied, values", [
    # Four prefixes copy 1+2+3+4=10 elements; unchanged [2,5] copies just two.
    (4, 10, "{1, 2, 3, 4}"),
    # Eight prefixes copy 1+2+3+4+5+6+7+8=36 elements.
    (8, 36, "{1, 2, 3, 4, 5, 6, 7, 8}"),
])
def test_freeze_growth_counts_unchanged_and_growing_owners(count, copied, values):
    # The unchanged half is red on unconditional freezing; the growing half pins the residual cost.
    unchanged = transpile(HEADER + SEED + HISTORY)
    _native(unchanged, f'''
    GeneratedStrategy strategy;
    auto feed = bars({count});
    strategy.run(feed.data(), {count});
    assert(strategy.last_error().empty());
    assert(pf_test_freezes == 1);
    assert(pf_test_copied_elements == 2);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{{2, 5}}));
''')
    growing = transpile(HEADER + "var a = array.new_int()\na.push(bar_index + 1)\n" + HISTORY)
    _native(growing, f'''
    GeneratedStrategy strategy;
    auto feed = bars({count});
    strategy.run(feed.data(), {count});
    assert(strategy.last_error().empty());
    assert((strategy.a == std::vector<int>{values}));
    assert(pf_test_freezes == {count});
    assert(pf_test_copied_elements == {copied});
    assert(&strategy._pf_collection_hist_a.at(1) != &strategy._pf_collection_hist_a.at(2));
''')


def _native_source(seed, signature, body, call, extra="", header=COOF_HEADER):
    return (header + f"var a = {seed}\n" + extra
            + f"mutate({signature}) =>\n    " + body.replace("\n", "\n    ") + "\n"
            + "armed = input.bool(false)\nif armed\n    mutate(" + call + ")\n" + HISTORY)


# Seed, signature, body, call/result/content on two restored branches, copied elements, extra declarations.
MUTATIONS = [
    # Push yields [2,5,7] or [2,5,9]; three freezes copy 2+3+3=8 elements.
    ("2, 5", "int value", "a.push(value)\na.size()", "7", 3, "{2, 5, 7}", "9", 3, "{2, 5, 9}", 8, ""),
    # Set replaces only the requested slot; 2+2+2=6 elements are frozen.
    ("2, 5", "int index, int value", "a.set(index, value)\na.get(index)", "1, 9", 9, "{2, 9}", "0, 7", 7, "{7, 5}", 6, ""),
    # Negative indices -1 and -2 normalize to 2 and 1 in a three-element seed.
    ("2, 5, 7", "int index, int value", "a.set(index, value)\na.get(index)", "-1, 9", 9, "{2, 5, 9}", "-2, 11", 11, "{2, 11, 7}", 9, ""),
    # Pop returns 7 and leaves [2,5] on either branch: 3+2+2=7 elements.
    ("2, 5, 7", "", "a.pop()", "", 7, "{2, 5}", "", 7, "{2, 5}", 7, ""),
    # Shift returns 2 and leaves [5,7] on either branch.
    ("2, 5, 7", "", "a.shift()", "", 2, "{5, 7}", "", 2, "{5, 7}", 7, ""),
    # Remove keeps the other two elements in their original order.
    ("2, 5, 7", "int index", "a.remove(index)", "1", 5, "{2, 7}", "0", 2, "{5, 7}", 7, ""),
    # Insert at 1 or 0 adds the supplied element without dropping either old one.
    ("2, 5", "int index, int value", "a.insert(index, value)\na.size()", "1, 7", 3, "{2, 7, 5}", "0, 9", 3, "{9, 2, 5}", 8, ""),
    # Unshift puts the parameter before both existing elements.
    ("2, 5", "int value", "a.unshift(value)\na.first()", "7", 7, "{7, 2, 5}", "9", 9, "{9, 2, 5}", 8, ""),
    # Clear freezes an empty but present version on both branches: 2+0+0=2 elements.
    ("2, 5", "", "a.clear()\na.size()", "", 0, "{}", "", 0, "{}", 2, ""),
    # Full fill overwrites both elements with the actual parameter.
    ("2, 5", "int value", "a.fill(value)\na.first()", "7", 7, "{7, 7}", "9", 9, "{9, 9}", 6, ""),
    # A defaulted start is zero; end 1 leaves the second element at 5.
    ("2, 5", "int value", "a.fill(value, index_to=1)\na.first()", "9", 9, "{9, 5}", "11", 11, "{11, 5}", 6, ""),
    # Sorting [3,1,2] produces [1,2,3] independently on both branches.
    ("3, 1, 2", "", "a.sort()\na.first()", "", 1, "{1, 2, 3}", "", 1, "{1, 2, 3}", 9, ""),
    # Reversal keeps the middle element and swaps the endpoints.
    ("3, 1, 2", "", "a.reverse()\na.first()", "", 2, "{2, 1, 3}", "", 2, "{2, 1, 3}", 9, ""),
    # Concat appends the distinct [7,9] source, so 2+4+4=10 elements are copied.
    ("2, 5", "", "a.concat(b)\na.size()", "", 4, "{2, 5, 7, 9}", "", 4, "{2, 5, 7, 9}", 10, "var b = array.from(7, 9)\n"),
    # Legacy assignment replaces current slot 1, retaining slot 0.
    ("2, 5", "int value", "a[1] := value\na.get(1)", "9", 9, "{2, 9}", "7", 7, "{2, 7}", 6, ""),
    # Legacy += adds 4 or 6 to the restored 5, yielding 9 or 11.
    ("2, 5", "int value", "a[1] += value\na.get(1)", "4", 9, "{2, 9}", "6", 11, "{2, 11}", 6, ""),
]


@pytest.mark.parametrize("row", MUTATIONS)
def test_observed_mutations_freeze_each_restored_branch_once(row):
    seed, signature, body, call, result, content, again, result2, content2, copied, extra = row
    cpp = transpile(_native_source("array.from(" + seed + ")", signature, body, call, extra))
    _shared(cpp)
    _native(cpp, f'''
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    assert(pf_test_freezes == 1);
    strategy.snapshot_script_state();
    assert(strategy.mutate({call}) == {result});
    assert((strategy.a == std::vector<int>{content}));
    const auto abandoned = strategy._pf_collection_hist_a.content_token();
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{content}));
    assert((strategy._pf_collection_hist_a.at(2) == std::vector<int>{{{seed}}}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 2);
    strategy.restore_script_state();
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{{{seed}}}));
    assert(strategy.mutate({again}) == {result2});
    assert(strategy._pf_collection_hist_a.content_token() != abandoned);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{content2}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == {copied});
    strategy.restore_script_state();
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    assert((strategy.a == std::vector<int>{{{seed}}}));
    assert(pf_test_freezes == 3);
''')


def test_rollback_restores_cache_and_logical_version_without_rewinding_allocator():
    cpp = transpile(_native_source("array.from(2, 5)", "int value", "a.set(1, value)\na.get(1)", "9"))
    _shared(cpp)
    # A=[2,5], abandoned B=[2,9], then C=[2,7]: each actual close of a new version adds one freeze.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    auto& h = strategy._pf_collection_hist_a;
    const auto baseline = h.content_token();
    assert(pf_test_freezes == 1);
    strategy.snapshot_script_state();
    strategy.mutate(9);
    const auto abandoned = h.content_token();
    open_history(strategy, false, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((h.at(1) == std::vector<int>{2, 9}));
    assert(pf_test_freezes == 2);
    strategy.restore_script_state();
    assert(h.content_token() == baseline);
    assert((strategy.a == std::vector<int>{2, 5}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 2);
    open_history(strategy, false, 0);
    close_history(strategy, 0);
    assert(pf_test_freezes == 2);
    strategy.mutate(7);
    assert(h.content_token() != abandoned);
    assert(h.content_token() != baseline);
    open_history(strategy, false, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((h.at(1) == std::vector<int>{2, 7}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 3);
    strategy.commit_script_state();
    strategy.mutate(11);
    open_history(strategy, false, 0);
    close_history(strategy, 0);
    assert(pf_test_freezes == 4);
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 7}));
    open_history(strategy, false, 0);
    close_history(strategy, 0);
    assert(pf_test_freezes == 4);
    assert(pf_test_copied_elements == 8);
''')


def test_second_run_starts_with_an_empty_history_and_a_fresh_cache():
    cpp = transpile(_native_source("array.from(2, 5)", "int value", "a.push(value)\na.size()", "7", header=HEADER))
    # Run one freezes [2,5] then [2,5,7]; run two must freeze its new [2,5] once: 2+3+2=7.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    assert(pf_test_freezes == 1);
    strategy.mutate(7);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    auto held = strategy._pf_collection_hist_a.reference(1);
    close_history(strategy, 0);
    assert((held.get() == std::vector<int>{2, 5, 7}));
    assert(pf_test_freezes == 2);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    assert((strategy.a == std::vector<int>{2, 5}));
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{2, 5}));
    assert(strategy._pf_collection_hist_a.is_na(3));
    assert((held.get() == std::vector<int>{2, 5, 7}));
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == 7);
''')


@pytest.mark.parametrize("replacement", [
    # Floating operands keep the replacement and its owner the same vector type.
    "array.from(value + 0.0, value + 1.0)",
    # Both floating switch arms pass through the same assignment boundary.
    "switch value\n        7 => array.from(7.0, 8.0)\n        => array.from(value + 0.0, value + 1.0)",
])
def test_whole_array_replacement_and_input_overrides_change_the_frozen_version(replacement):
    cpp = transpile(HEADER + 'value = input.int(7, "Replacement")\n'
                    + "var a = array.from(2.0, 5.0)\n"
                    + "if bar_index == 2\n    a := " + replacement + "\n" + HISTORY)
    _shared(cpp)
    # Default 7 gives [7,8]; override 11 gives [11,12]; each run freezes two two-element versions.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(5);
    strategy.run(feed.data(), 5);
    assert(strategy.last_error().empty());
    // Closes A,A,B,B,B leave bar 3's B at offset 1 and bar 1's A at offset 3.
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<double>{7.0, 8.0}));
    assert((strategy._pf_collection_hist_a.at(3) == std::vector<double>{2.0, 5.0}));
    // A=[2,5] and B=[7,8] each freeze once, copying 2+2 elements.
    assert(pf_test_freezes == 2);
    assert(pf_test_copied_elements == 4);
    strategy.set_input("Replacement", "11");
    strategy.run(feed.data(), 5);
    assert(strategy.last_error().empty());
    // The new run keeps A=[2,5] and replaces B with [11,12] on bar 2.
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<double>{11.0, 12.0}));
    assert((strategy._pf_collection_hist_a.at(3) == std::vector<double>{2.0, 5.0}));
    // Two more two-element versions make four freezes and eight elements in total.
    assert(pf_test_freezes == 4);
    assert(pf_test_copied_elements == 8);
''')


@pytest.mark.parametrize("initializer", [
    # The computed selector reaches the first switch arm only for choice zero.
    pytest.param("switch selected\n    1 => array.from(2, 5)\n    => array.from(7, 9)\n", id="switch"),
    # The same computed selector drives the if-expression arm.
    pytest.param("if selected == 1\n    array.from(2, 5)\nelse\n    array.from(7, 9)\n", id="if"),
])
@pytest.mark.parametrize("choice, values", [
    # Choice 0 computes selected=1 and initializes [2,5] once at the declaration.
    (0, "{2, 5}"),
    # Choice 1 computes selected=2 and initializes [7,9] once at the declaration.
    (1, "{7, 9}"),
])
def test_selected_first_initialization_and_its_latch_restore_together(
    initializer, choice, values,
):
    source = (COOF_HEADER + f'''choice = input.int({choice}, "Choice")
selected = choice + 1
var array<int> a = ''' + initializer + HISTORY)
    cpp = transpile(source)
    _shared(cpp)
    # A checkpoint before initialization restores empty storage, token, history and the once latch.
    _native(cpp, f'''
    GeneratedStrategy strategy;
    auto feed = bars(1);
    strategy.snapshot_script_state();
    strategy.on_source_bar(feed[0]);
    assert((strategy.a == std::vector<int>{values}));
    assert(pf_test_freezes == 1);
    strategy.restore_script_state();
    assert(strategy.a.empty());
    assert(strategy._pf_collection_hist_a.is_na(1));
    strategy.on_source_bar(feed[0]);
    assert((strategy.a == std::vector<int>{values}));
    assert(pf_test_freezes == 2);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    assert(pf_test_freezes == 2);
    assert(pf_test_copied_elements == 4);
''')


@pytest.mark.parametrize("choice", [
    0,  # The explicit arm selects [2,5], but supplies no collection TypeSpec.
    1,  # The default arm selects [7,9], with the same unresolved declaration.
])
def test_untyped_selection_keeps_the_eager_history_output(choice, monkeypatch):
    # A resolved seed must share while the unresolved selection keeps every byte.
    _shared(transpile(COOF_HEADER + SEED + HISTORY))
    source = COOF_HEADER + f'''choice = input.int({choice}, "Choice")
var a = switch choice
    0 => array.from(2, 5)
    => array.from(7, 9)
''' + HISTORY
    cpp = transpile(source)
    # With no collection TypeSpec, a[3] registers scalar Series<double> storage.
    assert "    Series<double> a;" in cpp
    assert "_pf_collection_hist_a" not in cpp
    assert "class _PFCachedCollectionHistory" not in cpp
    assert cpp == _eager_cpp(source, monkeypatch)


@pytest.mark.parametrize("operation", [
    # Both receiver spellings reach the same live array at offset zero.
    "array.set(a[offset], 1, 9)",
    "(a[offset]).set(1, 9)",
])
def test_dynamic_changed_zero_writes_live_array_and_nonzero_keeps_error_order(operation):
    cpp = transpile('//@version=6\nstrategy("frozen arrays", max_bars_back=4)\n'
                    + SEED + f'''offset = input.int(0, "Offset")
if bar_index == 2
    {operation}
''' + HISTORY)
    # Four slots retain offsets 0..3; at bar 2, slot 3 has not been populated.
    _shared(cpp)
    # One authored set has one dirty site in either receiver spelling.
    assert cpp.count(WRITER + ".save_set(__pf_array, (size_t)__pf_array_index)") == 1
    # Offset 0 writes [2,9]; offset 1 is immutable; offset 3 is missing before any write.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(5);
    strategy.run(feed.data(), 5);
    assert(strategy.last_error().empty());
    assert((strategy.a == std::vector<int>{2, 9}));
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{2, 9}));
    assert((strategy._pf_collection_hist_a.at(3) == std::vector<int>{2, 5}));
    // Five closes A,A,B,B,B freeze two two-element versions.
    assert(pf_test_freezes == 2);
    assert(pf_test_copied_elements == 4);
    assert(&strategy._pf_collection_hist_a.changed(0, strategy.a) == &strategy.a);
    strategy.set_input("Offset", "1");
    strategy.run(feed.data(), 5);
    assert(strategy.last_error() == "Cannot modify the elements of a historical array or any slices of that array. Instead of modifying an array referenced by an ID retrieved with the `[]` operator, create a shallow copy of the array with `array.copy()`, then modify the copy or a slice of that copy.");
    assert((strategy.a == std::vector<int>{2, 5}));
    // The failed receiver adds only the seed's freeze, before bar 2 opens.
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == 6);
    // A runtime failure seals the host; a fresh host reaches the next receiver error.
    GeneratedStrategy missing;
    missing.set_input("Offset", "3");
    missing.run(feed.data(), 5);
    assert(missing.last_error() == "Cannot call array methods when id of array is na.");
    // Bars 0/1 ran; bar 2 opened, but missing slot 3 stopped the set before it wrote.
    assert((missing.a == std::vector<int>{2, 5}));
    assert((missing._pf_collection_hist_a.at(1) == std::vector<int>{2, 5}));
    assert(missing._pf_collection_hist_a.is_na(3));
    // The fresh host adds one two-element seed freeze: four freezes, eight elements.
    assert(pf_test_freezes == 4);
    assert(pf_test_copied_elements == 8);
''')


def test_exhausted_allocator_disables_reuse_without_wrapping_a_token():
    cpp = transpile(_native_source("array.from(2, 5)", "int value", "a.set(1, value)\na.get(1)", "9"))
    _shared(cpp)
    # Start two allocations from exhaustion: run identity consumes one, initialization the last.
    assert cpp.count("uint64_t next_ = 1;") == 1
    cpp = cpp.replace("uint64_t next_ = 1;", "uint64_t next_ = std::numeric_limits<uint64_t>::max() - 2;")
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    assert(pf_test_freezes == 3);
    strategy.snapshot_script_state();
    strategy.mutate(9);
    assert(strategy._pf_collection_hist_a.content_token() == 0);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{2, 9}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 5);
    strategy.restore_script_state();
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    assert((strategy.a == std::vector<int>{2, 5}));
    assert(pf_test_freezes == 6);
    assert(pf_test_copied_elements == 12);
''')


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_frozen_floating_bits_survive_sharing_and_rollback(opt):
    cpp = transpile(_native_source("array.from(1.0, 2.0)", "float first, float second",
                                   "a.set(0, first)\na.set(1, second)\na.size()", "3.0, 4.0"))
    _shared(cpp)
    # Frozen -0 and the exact quiet-NaN payload retain their bits after live replacement and restore.
    _native(cpp, r'''
    auto from_bits = [](uint64_t bits) { double value; std::memcpy(&value, &bits, 8); return value; };
    auto bits_of = [](double value) { uint64_t bits; std::memcpy(&bits, &value, 8); return bits; };
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    strategy.mutate(from_bits(UINT64_C(0x8000000000000000)), from_bits(UINT64_C(0x7ff8000000000042)));
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    auto& h = strategy._pf_collection_hist_a;
    assert(&h.at(1) == &h.at(2));
    assert(bits_of(h.at(1).at(0)) == UINT64_C(0x8000000000000000));
    assert(bits_of(h.at(1).at(1)) == UINT64_C(0x7ff8000000000042));
    assert(pf_test_freezes == 2);
    strategy.snapshot_script_state();
    strategy.mutate(9.0, 11.0);
    open_history(strategy, false, 0);
    close_history(strategy, 0);
    strategy.restore_script_state();
    assert(bits_of(strategy.a.at(0)) == UINT64_C(0x8000000000000000));
    assert(bits_of(strategy.a.at(1)) == UINT64_C(0x7ff8000000000042));
    open_history(strategy, false, 0);
    close_history(strategy, 0);
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == 6);
''', opt=opt)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("seed, signature, call, again, body, expected, expected2, value_type, pine_call, copied", [
    # A bool set materializes false; a later true set cannot change the frozen false bit.
    ("array.from(true, false)", "bool value", "false", "true", "a.set(0, value)\na.size()",
     "{false, false}", "{true, false}", "bool", "false", 6),
    # A string argument preserves its embedded NUL and length in the detached payload.
    ('array.from("first", "tail")', "string value", 'std::string("a\\0b", 3)', 'std::string("n\\0w", 3)',
     "a.set(0, value)\na.size()", '{std::string("a\\0b", 3), "tail"}', '{std::string("n\\0w", 3), "tail"}', "std::string", '"first"', 6),
    # Pushing time widens the vector; both branches keep the exact integer above 2^53.
    ("array.new_int()", "int value", "7", "11", "a.push(time)\na.push(value)\na.size()",
     "{9007199254740993LL, 7}", "{9007199254740993LL, 11}", "int64_t", "7", 4),
    # Color fill keeps both packed integer values, including every alpha bit.
    ("array.new<color>(2, color.red)", "color value", "0x11223344LL", "0x55667788LL",
     "a.fill(value)\na.size()", "{0x11223344LL, 0x11223344LL}", "{0x55667788LL, 0x55667788LL}",
     "int64_t", "color.blue", 6),
])
def test_primitive_storage_remains_exact_across_frozen_versions(opt, seed, signature, call, again, body, expected, expected2, value_type, pine_call, copied):
    cpp = transpile(_native_source(seed, signature, body, pine_call))
    _shared(cpp)
    # One seed and two distinct branches freeze three times; the empty wide-int seed copies no elements.
    _native(cpp, f'''
    ClockedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    strategy.set_test_timestamp(9007199254740993LL);
    strategy.snapshot_script_state();
    strategy.mutate({call});
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<{value_type}>{expected}));
    strategy.restore_script_state();
    strategy.mutate({again});
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<{value_type}>{expected2}));
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == {copied});
''', opt=opt)


def test_operand_mutation_before_a_failed_index_check_still_invalidates_history():
    cpp = transpile(_native_source("array.from(2, 5)", "int index", "a.set(index, a.pop())\na.size()", "0"))
    _shared(cpp)
    # Pop runs first: invalid index 1 leaves [2], while valid index 0 leaves [5].
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    bool stopped = false;
    try { strategy.mutate(1); }
    catch (const std::exception& error) {
        stopped = true;
        assert(std::string(error.what()) == "Index 1 is out of bounds. Array size is 1");
    }
    assert(stopped);
    assert((strategy.a == std::vector<int>{2}));
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{2}));
    close_history(strategy, 0);
    strategy.restore_script_state();
    assert(strategy.mutate(0) == 1);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{5}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == 4);
''')


def test_distinct_owners_keep_independent_caches_on_the_same_version_clock():
    cpp = transpile(_native_source("array.from(2, 5)", "int value", "a.push(value)\na.size()", "7",
                                   "var b = array.from(2, 5)\nb_old = na(b[3]) ? -1 : (b[3]).size()\n"))
    _shared(cpp)
    # Two owners initially freeze separately; changing only a adds one three-element payload.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    auto& first = strategy._pf_collection_hist_a;
    auto& second = strategy._pf_collection_hist_b;
    assert(pf_test_freezes == 2);
    assert(&first.at(1) != &second.at(1));
    assert(&first.at(1) == &first.at(2));
    assert(&second.at(1) == &second.at(2));
    strategy.mutate(7);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    second.open(strategy.b, true, strategy._pf_history_versions);
    second.close(strategy.b, strategy._pf_history_versions);
    open_history(strategy, true, 0);
    assert((first.at(1) == std::vector<int>{2, 5, 7}));
    assert((second.at(1) == std::vector<int>{2, 5}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == 7);
''')


def test_each_callable_instance_marks_its_global_array_writes():
    cpp = transpile(COOF_HEADER + '''var a = array.new_int()
step(int value) =>
    var int count = 0
    count += 1
    a.push(value + count)
    a.size()
armed = input.bool(false)
if armed
    step(2)
    step(5)
''' + HISTORY)
    _shared(cpp)
    assert cpp.count(WRITER + ".push(a,") == 2  # Both callable instances share the one global owner.
    # Independent counters add 1: [3,6] on one branch and [10,8] on the restored branch.
    _native(cpp, r'''
    GeneratedStrategy strategy;
    auto feed = bars(3);
    strategy.run(feed.data(), 3);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    strategy.step_cs0(2);
    strategy.step_cs1(5);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{3, 6}));
    close_history(strategy, 0);
    strategy.restore_script_state();
    strategy.step_cs1(9);
    strategy.step_cs0(7);
    open_history(strategy, true, 0);
    close_history(strategy, 0);
    open_history(strategy, true, 0);
    assert((strategy._pf_collection_hist_a.at(1) == std::vector<int>{10, 8}));
    close_history(strategy, 0);
    assert(pf_test_freezes == 3);
    assert(pf_test_copied_elements == 4);
''')


HISTORY_TRACE_NAMES = ("current_value", "previous_value", "older_value")


def _scheduled_source(units, eager=False):
    source = schedule._source(units).replace("callbacks += 1\n", '''callbacks += 1
var a = array.from(2, 5)
if bar_index == 2 or bar_index == 4
    a.set(1, chartWalks)
previous = na(a[1]) ? -1 : (a[1]).get(1)
older = na(a[3]) ? -1 : (a[3]).get(1)
''')
    if eager:
        # A harmless live-array argument selects the original unconditional freeze path.
        source += "observe(array<int> values) => values.size()\nobserved = observe(a)\n"
    return source + '''// @pf-trace current_value=a.get(1)
// @pf-trace previous_value=previous
// @pf-trace older_value=older
'''


def _scheduled_run(engine, base_dir, feed, aux, key, units, eager=False):
    work = base_dir / key
    work.mkdir()
    (work / "inputs.json").write_text(json.dumps({
        "input_tf": "15", "script_tf": "15",
        "aux_security_ohlcv_csv": str(aux), "aux_security_input_tf": "1",
        "runtime_overrides": {
            "mintick": schedule.TICK, "pointvalue": 1, "qty_step": 1,
            "currency": "USD", "timezone": "UTC",
            "syminfo_metadata": {"mincontract": 1},
        },
    }), encoding="utf-8")
    return ok(execute_all(engine, feed, base_dir, {
        key: Build(_scheduled_source(units, eager), trace=True),
    }), key)


def _scheduled_frames(records, chart):
    names = schedule.TRACE_NAMES + HISTORY_TRACE_NAMES
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


SCHEDULED_VALUES = [
    # Bar 0 holds the seed 5 and has no prior slots.
    (5, -1, -1),
    # Bar 1 sees the seed from bar 0.
    (5, 5, -1),
    # Bar 2 writes walk 3; its previous slot still holds 5.
    (3, 5, -1),
    # Both executions of bar 3 retain 3, with bar 0's 5 three slots back.
    (3, 3, 5),
    # Bar 4 writes walk 5 and still sees 3 from bar 3 and 5 from bar 1.
    (5, 3, 5),
    # Bar 5 sees 5 from bar 4 and 3 from bar 2.
    (5, 5, 3),
    # Bar 6 sees 5 from bar 5 and 3 from bar 3.
    (5, 5, 3),
    # Bar 7 sees the repeated 5 at both historical offsets.
    (5, 5, 5),
]


def test_shared_history_matches_eager_execution_during_real_fill_callbacks(tmp_path):
    # LAB-ONLY: use the established funded control and the real late margin callback.
    _shared(transpile(_scheduled_source(3)))
    assert "class _PFCachedCollectionHistory" not in transpile(_scheduled_source(3, eager=True))
    engine = skip_unless_e2e_env()
    feed, aux, chart, _minutes = schedule._feeds(tmp_path)
    control = _scheduled_run(engine, tmp_path, feed, aux, "control", 2)
    control_rows = _scheduled_frames(control.traces["default"], chart)
    # Completed 30m requests advance every two bars; the finer 5m request advances three buckets.
    higher = [0, 1, 1, 2, 2, 3, 3, 4]
    finer = [1, 4, 7, 10, 13, 16, 19, 22]
    # The two-unit position stays funded; each ordinary chart step adds one walk.
    positions = [0, 2, 2, 2, 2, 2, 2, 2]
    walks = [1, 2, 3, 4, 5, 6, 7, 8]
    for index, bar in enumerate(chart):
        for row in control_rows[bar[0]]:
            assert tuple(row[name] for name in HISTORY_TRACE_NAMES) == SCHEDULED_VALUES[index]
            assert (row["higher"], row["finer"]) == (higher[index], finer[index])
            assert row["chart_walks"] == walks[index]
            assert row["position"] == positions[index]
            assert row["closed"] == row["margin"] == row["margin_price"] == 0
    assert len(control_rows[chart[3][0]]) == 1  # The funded control has no late fill on bar 3.

    eager = _scheduled_run(engine, tmp_path, feed, aux, "eager", 3, eager=True)
    shared = _scheduled_run(engine, tmp_path, feed, aux, "shared", 3)
    assert shared.trades == eager.trades
    assert shared.traces == eager.traces
    rows = _scheduled_frames(shared.traces["default"], chart)
    late = rows[chart[3][0]]
    # One late margin fill reduces three units to two and adds exactly one callback.
    assert [(row["position"], row["closed"], row["margin"]) for row in late] == [(3, 0, 0), (2, 1, 1)]
    assert [row["chart_walks"] for row in late] == [4, 5]
    assert [tuple(row[name] for name in HISTORY_TRACE_NAMES) for row in late] == [(3, 3, 5), (3, 3, 5)]
    assert late[1]["margin_price"] == 100_000_000.25
    assert late[1]["callbacks"] - late[0]["callbacks"] == 1
    assert rows[chart[4][0]][0]["callbacks"] - late[1]["callbacks"] == 1
    assert sum(map(len, rows.values())) - sum(map(len, control_rows.values())) == 1
    # The closed trade remains after bar 3 while every historical slot follows its chart bar.
    positions = [0, 3, 3, 3, 2, 2, 2, 2]
    closed = [0, 0, 0, 0, 1, 1, 1, 1]
    for index, bar in enumerate(chart):
        for execution, row in enumerate(rows[bar[0]]):
            is_late = index == 3 and execution == 1
            assert tuple(row[name] for name in HISTORY_TRACE_NAMES) == SCHEDULED_VALUES[index]
            assert (row["higher"], row["finer"]) == (higher[index], finer[index])
            assert row["position"] == (2 if is_late else positions[index])
            assert row["closed"] == row["margin"] == (1 if is_late else closed[index])
