"""Primitive array checkpoint costs and exact rollback through emitted writes."""

from __future__ import annotations

import json

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import tables
from pineforge_codegen.codegen.base import CodeGen
from pineforge_codegen.collection_history import ARRAY_CHANGING_METHODS
from tests._compile import run_emitted_tu
from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests import test_e2e_security_coof_restore_witness as schedule


HEADER = '//@version=6\nstrategy("array checkpoints", calc_on_order_fills=true)\n'


def _snapshot(cpp):
    return cpp.split("    void snapshot_script_state() override {\n", 1)[1].split(
        "    void restore_script_state() override {", 1)[0]


def _enrolled(cpp, name="a", generation="_pf_next_generation"):
    # A stable root contributes an address token, never its vector payload.
    snapshot = _snapshot(cpp)
    assert f"            {name},\n" not in snapshot
    assert "class _PFArrayJournal" in cpp
    assert "        _pf_script_state_checkpoint_.emplace(_PFScriptState{\n" in snapshot
    assert f"            {{&this->{name}, {generation}}},\n" in snapshot


def _eager_cpp(source, monkeypatch):
    def eager(self):
        self._array_journal_members = {}
        self._array_journal_calls = {}

    with monkeypatch.context() as patch:
        patch.setattr(CodeGen, "_prepare_array_journal", eager, raising=False)
        return transpile(source)


def test_array_journal_catalogue_is_closed():
    delta = getattr(tables, "ARRAY_JOURNAL_DELTA_METHODS", frozenset())
    full = getattr(tables, "ARRAY_JOURNAL_FULL_METHODS", frozenset())
    reads = getattr(tables, "ARRAY_JOURNAL_READ_METHODS", frozenset())
    # Twelve destructive families partition into seven delta-capable and five full-preimage operations.
    assert delta | full == ARRAY_CHANGING_METHODS
    assert not delta & full
    assert not ARRAY_CHANGING_METHODS & reads
    assert ARRAY_CHANGING_METHODS | reads == set(tables.ARRAY_METHODS)


@pytest.mark.parametrize("operation, fragment", [
    # Method push appends one value through the length-record adapter.
    ("a.push(7)", "this->_pf_array_journal.push(a, 7)"),
    # Namespace push reaches exactly the same single mutation boundary.
    ("array.push(a, 7)", "this->_pf_array_journal.push(a, 7)"),
    # Checked set saves one normalized slot after argument evaluation and checks.
    ("a.set(1, 9)", "this->_pf_array_journal.save_set(__pf_array, (size_t)__pf_array_index)"),
    # Reordered keywords retain that same checked set boundary for index -1.
    ("array.set(value=9, id=a, index=-1)", "this->_pf_array_journal.save_set(__pf_array, (size_t)__pf_array_index)"),
    # Pop records the last index and its removed value.
    ("a.pop()", "this->_pf_array_journal.save_remove(__pf_array, __pf_array.size()-1)"),
    # Shift records index zero and its removed value.
    ("a.shift()", "this->_pf_array_journal.save_remove(__pf_array, 0)"),
    # Remove uses the index accepted by the existing bounds checker.
    ("a.remove(1)", "this->_pf_array_journal.save_remove(__pf_array, (size_t)__pf_array_index)"),
    # Insert records the validated insertion position.
    ("a.insert(1, 7)", "this->_pf_array_journal.insert(__pf_array,"),
    # Unshift inserts at begin and shares the insertion adapter.
    ("a.unshift(7)", "this->_pf_array_journal.insert(a, a.begin(), 7)"),
    # Clear saves the full lost sequence before clearing it.
    ("a.clear()", "this->_pf_array_journal.clear(a)"),
    # Full fill preserves its original begin/end/value evaluation and records a preimage.
    ("a.fill(7)", "this->_pf_array_journal.fill(a, a.begin(), a.end(), 7)"),
    # A defaulted bounded fill records only after its existing range checks.
    ("a.fill(7, index_to=1)", "this->_pf_array_journal.fill(__pf_array,"),
    # Sort saves the original permutation instead of sorting again during undo.
    ("a.sort()", "this->_pf_array_journal.sort(a, a.begin(), a.end())"),
    # Descending sort supplies the greater comparator on its journaled branch.
    ("a.sort(order.descending)", "this->_pf_array_journal.sort(a, a.begin(), a.end(), std::greater<>())"),
    # Reverse uses the same conservative full-preimage policy.
    ("a.reverse()", "this->_pf_array_journal.reverse(a, a.begin(), a.end())"),
    # Legacy assignment records one checked lvalue immediately before the write.
    ("a[1] := 9", "this->_pf_array_journal.save_set(_pf_history_array, (size_t)_pf_history_offset)"),
    # Native += keeps its RHS-before-LHS sequencing and saves that same lvalue once.
    ("a[1] += 9", "this->_pf_array_journal.save_set(_pf_history_array, (size_t)_pf_history_offset)"),
])
def test_direct_mutation_has_token_and_one_write_site(operation, fragment):
    cpp = transpile(HEADER + "var a = array.from(2, 5)\n" + operation + "\n")
    _enrolled(cpp)
    assert cpp.count(fragment) == 1  # One source operation emits one mutation-boundary call.
    assert "this->_pf_array_journal.replace(a," in cpp


def test_append_records_do_not_move_payload_copying_out_of_the_checkpoint():
    cpp = transpile(HEADER + "var a = array.from(2, 5)\na.push(7)\n")
    _enrolled(cpp)
    push = cpp.split("    void push(std::vector<T>& array, V&& value) {", 1)[1].split(
        "    template <", 1)[0]
    # Append stores one length and owner pointer, with no copy of the untouched prefix.
    assert "const size_t size = array.size();" in push
    assert "[owner = &array, size]()" in push
    assert "owner->resize(size);" in push
    assert "save_full" not in push and "old = array" not in push
    # Dense writes keep their full preimage immediately before the original operation.
    for operation in (
        "array.clear();",
        "std::fill(first, last, value);",
        "std::sort(first, last, compare...);",
        "std::reverse(first, last);",
        "return array.insert(position, first, last);",
        "array = std::forward<V>(value);",
    ):
        assert "save_full(array);\n        " + operation in cpp


FALLBACKS = [
    # Passing the array itself to a function escapes the direct-root proof.
    HEADER + "var a = array.from(2, 5)\nobserve(array<int> values) => values.size()\na.push(7)\nr = observe(a)\n",
    # Any UDT keeps the script on its existing handle-aware checkpoint path.
    HEADER + "type Item\n    int value\nvar item = Item.new(1)\nvar a = array.from(2, 5)\na.push(7)\n",
    # Map-bearing scripts retain their existing checkpoint traits.
    HEADER + "var m = map.new<string, int>()\nvar a = array.from(2, 5)\na.push(7)\n",
    # Matrix-bearing scripts retain their existing checkpoint traits.
    HEADER + "var m = matrix.new<int>(1, 1, 0)\nvar a = array.from(2, 5)\na.push(7)\n",
    # Literal false does not enroll any root.
    HEADER.replace("calc_on_order_fills=true", "calc_on_order_fills=false") + "var a = array.from(2, 5)\na.push(7)\n",
    # An omitted declaration does not enroll any root.
    HEADER.replace(", calc_on_order_fills=true", "") + "var a = array.from(2, 5)\na.push(7)\n",
    # One does not equal two, so the constant expression disables fill recalculation.
    HEADER.replace("calc_on_order_fills=true", "calc_on_order_fills=1 == 2") + "var a = array.from(2, 5)\na.push(7)\n",
    # Varip belongs outside rollback and must not acquire a journal.
    HEADER + "varip a = array.from(2, 5)\na.push(7)\n",
    # A later whole-vector replacement invalidates enrollment from the outset.
    HEADER + "var a = array.from(2, 5)\na := array.from(7, 9)\na.push(11)\n",
    # A bare-value alias escapes even when the original also has a direct push.
    HEADER + "var a = array.from(2, 5)\nalias = a\nalias.push(7)\na.push(9)\n",
    # A retained collection history keeps its original storage and snapshots.
    HEADER + "var a = array.from(2, 5)\na.push(7)\nr = (a[1]).size()\n",
    # A helper makes the array reachable from a requested payload.
    HEADER + "var a = array.from(2, 5)\na.push(7)\nf() => a.size()\nr = request.security(syminfo.tickerid, \"60\", f())\n",
    # A computed global preserves that requested dependency transitively.
    HEADER + "var a = array.from(2, 5)\na.push(7)\nx = a.size()\nr = request.security(syminfo.tickerid, \"60\", x)\n",
    # Reading alone is not mutation reachability.
    HEADER + "var a = array.from(2, 5)\nr = a.size()\n",
    # An uncalled helper does not make its global mutation reachable.
    HEADER + "var a = array.from(2, 5)\nf() =>\n    a.push(7)\n    a.size()\nr = a.size()\n",
    # Callable-local storage has no admitted stable top-level owner.
    HEADER + "f() =>\n    var a = array.from(2, 5)\n    a.push(7)\n    a.size()\nr = f()\n",
    # Self-concat has unproved overlapping iterator behavior.
    HEADER + "var a = array.from(2, 5)\na.concat(a)\n",
    # A read used as an lvalue is outside the instrumented element-assignment path.
    HEADER + "var a = array.from(2, 5)\na.push(7)\na.get(0) := 9\n",
    # End-access lvalues likewise require the original complete checkpoint.
    HEADER + "var a = array.from(2, 5)\na.push(7)\na.last() += 1\n",
    # A same-spelled parameter prevents the unique-binding proof.
    HEADER + "var a = array.from(2, 5)\nf(int a) => a + 1\na.push(7)\nr = f(2)\n",
    # An authored method named push must keep its own dispatch and effects.
    HEADER + "var a = array.from(2, 5)\nmethod push(array<int> values, int value) =>\n    values.set(0, value)\na.push(7)\n",
]


@pytest.mark.parametrize("source", FALLBACKS)
def test_ineligible_sources_keep_eager_output_byte_for_byte(source, monkeypatch):
    cpp = transpile(source)
    assert "_pf_array_journal" not in cpp
    assert cpp == _eager_cpp(source, monkeypatch)


def test_scalar_candidate_does_not_register_array_history_during_planning(monkeypatch):
    source = HEADER + """var int count = 1
values = array.from(2, 5)
previous = values[1]
size = na(previous) ? count : previous.size()
"""
    prepare = CodeGen._prepare_array_journal

    def plan(self):
        calls = []
        history_names = self._array_history_value_names

        def counted_history_names():
            calls.append(True)
            return history_names()

        with monkeypatch.context() as patch:
            patch.setattr(self, "_array_history_value_names", counted_history_names)
            prepare(self)
        # The only persistent declaration is scalar, so no array candidate needs registration.
        assert calls == []

    monkeypatch.setattr(CodeGen, "_prepare_array_journal", plan)
    cpp = transpile(source)
    assert cpp == _eager_cpp(source, monkeypatch)


def test_eager_checkpoint_fragment_and_enrolled_token(monkeypatch):
    source = HEADER + "var a = array.from(2, 5)\na.push(7)\n"
    eager = _eager_cpp(source, monkeypatch)
    # The declaration inventory is a, its aggregate latch, then the input latch.
    assert _snapshot(eager) == (
        "        _pf_script_state_checkpoint_.emplace(_PFScriptState{\n"
        "            a,\n"
        "            _var_initialized,\n"
        "            _inputs_initialized_,\n"
        "        });\n"
        "    }\n\n"
    )
    _enrolled(transpile(source))


def test_unclassified_write_falls_back_for_the_whole_root(monkeypatch):
    from pineforge_codegen.codegen import base

    source = HEADER + "var a = array.from(2, 5)\na.push(7)\na.set(0, 9)\n"
    _enrolled(transpile(source))
    monkeypatch.setattr(base, "ARRAY_JOURNAL_DELTA_METHODS", tables.ARRAY_JOURNAL_DELTA_METHODS - {"set"})
    # Losing coverage of set must restore the payload copy, including the earlier push.
    cpp = transpile(source)
    assert "            a,\n" in _snapshot(cpp)
    assert "_pf_array_journal" not in cpp


def test_trace_writes_and_unrelated_requests_are_covered():
    source = HEADER + """var a = array.from(2, 5)
a.push(7)
r = request.security(syminfo.tickerid, "60", close)
// @pf-trace removed=a.pop()
"""
    cpp = transpile(source)
    _enrolled(cpp)
    assert "this->_pf_array_journal.save_remove(__pf_array, __pf_array.size()-1)" in cpp


def test_journal_names_do_not_hide_authored_bindings():
    source = HEADER + """var a = array.from(2, 5)
_PFArrayJournal = input.int(1)
_pf_array_journal = input.int(7)
a.push(_pf_array_journal + _PFArrayJournal)
"""
    cpp = transpile(source)
    _enrolled(cpp)
    assert "class _PFArrayJournal__pf2" in cpp
    assert "this->_pf_array_journal__pf2.push(a," in cpp


def test_checkpoint_generation_name_does_not_shadow_copied_state():
    source = HEADER + '''var a = array.from(2, 5)
var int _pf_next_generation = 7
mutate(int value) =>
    a.push(value)
    a.size()
armed = input.bool(false)
if armed
    mutate(9)
'''
    cpp = transpile(source)
    _enrolled(cpp, generation="_pf_next_generation__pf2")
    assert "const auto _pf_next_generation__pf2 = " in _snapshot(cpp)
    assert "            _pf_next_generation,\n" in _snapshot(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
static_assert(!std::is_copy_constructible_v<GeneratedStrategy>);
static_assert(!std::is_move_constructible_v<GeneratedStrategy>);
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    strategy._pf_next_generation = 11;
    strategy.mutate(9);
    strategy.restore_script_state();
    assert(strategy._pf_next_generation == 7);
    assert((strategy.a == std::vector<int>{2, 5}));
    std::cout << "ok\n";
}
'''
    # The ordinary scalar's baseline is 7, independent of the coordinator's generation number.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="checkpoint name collision") == "ok\n"


def _native_source(seed, signature, body, call, extra=""):
    return (HEADER + f"var a = array.from({seed})\n" + extra
            + f"mutate({signature}) =>\n    " + body.replace("\n", "\n    ") + "\n"
            + "armed = input.bool(false)\nif armed\n    mutate(" + call + ")\n")


# seed, parameters, body, first call/result/content, second call/result/content.
MUTATIONS = [
    # Appending 7 or 9 adds just that suffix to the checkpointed [2,5].
    ("push", "2, 5", "int value", "a.push(value)\na.size()", "7", 3, [2, 5, 7], "9", 3, [2, 5, 9]),
    # Each set replaces its selected slot and preserves the other element.
    ("set", "2, 5", "int index, int value", "a.set(index, value)\na.get(index)", "1, 9", 9, [2, 9], "0, 7", 7, [7, 5]),
    # -1 normalizes to the last index; -2 selects the middle of [2,5,7].
    ("negative_set", "2, 5, 7", "int index, int value", "a.set(index, value)\na.get(index)", "-1, 9", 9, [2, 5, 9], "-2, 11", 11, [2, 11, 7]),
    # Pop removes and returns the final 7 on each replay of the same baseline.
    ("pop", "2, 5, 7", "", "a.pop()", "", 7, [2, 5], "", 7, [2, 5]),
    # Insert at 1 shifts 5 right; insert at 0 shifts both original elements.
    ("insert", "2, 5", "int index, int value", "a.insert(index, value)\na.size()", "1, 7", 3, [2, 7, 5], "0, 9", 3, [9, 2, 5]),
    # Removing 1 returns 5; removing 0 returns 2 and keeps the suffix order.
    ("remove", "2, 5, 7", "int index", "a.remove(index)", "1", 5, [2, 7], "0", 2, [5, 7]),
    # Clear loses the whole seed, then the new append becomes the sole element.
    ("clear_push", "2, 5", "int value", "a.clear()\na.push(value)\na.size()", "7", 1, [7], "9", 1, [9]),
    # Sort changes [3,1,2] to [1,2,3]; rollback must recover the original permutation.
    ("sort", "3, 1, 2", "", "a.sort()\na.first()", "", 1, [1, 2, 3], "", 1, [1, 2, 3]),
    # Descending sort orders [3,1,2] as [3,2,1] and returns its first 3.
    ("sort_descending", "3, 1, 2", "", "a.sort(order.descending)\na.first()", "", 3, [3, 2, 1], "", 3, [3, 2, 1]),
    # Reverse maps [3,1,2] to [2,1,3], with no arithmetic undo.
    ("reverse", "3, 1, 2", "", "a.reverse()\na.first()", "", 2, [2, 1, 3], "", 2, [2, 1, 3]),
    # A full fill overwrites both cells with its actual parameter.
    ("fill", "2, 5", "int value", "a.fill(value)\na.first()", "7", 7, [7, 7], "9", 9, [9, 9]),
    # [1,3) overwrites the suffix; [0,1) overwrites only the first element.
    ("range_fill", "2, 5, 7", "int value, int first, int last", "a.fill(value, first, last)\na.size()", "9, 1, 3", 3, [2, 9, 9], "11, 0, 1", 3, [11, 5, 7]),
    # Unshift prepends 7 or 9 before both saved elements.
    ("unshift", "2, 5", "int value", "a.unshift(value)\na.first()", "7", 7, [7, 2, 5], "9", 9, [9, 2, 5]),
    # Shift removes and returns the initial 2, leaving [5,7].
    ("shift", "2, 5, 7", "", "a.shift()", "", 2, [5, 7], "", 2, [5, 7]),
    # Push then replace the middle then pop leaves only that middle replacement.
    ("nested", "2, 5", "int value", "a.push(value)\na.set(1, value + 2)\na.pop()", "7", 7, [2, 9], "9", 9, [2, 11]),
    # The legacy assignment writes the current checked slot, exactly like its old lowering.
    ("element", "2, 5", "int value", "a[1] := value\na.get(1)", "9", 9, [2, 9], "7", 7, [2, 7]),
    # Compound += uses the prior 5, so adding 4 gives 9 and adding 6 gives 11.
    ("compound_element", "2, 5", "int value", "a[1] += value\na.get(1)", "4", 9, [2, 9], "6", 11, [2, 11]),
    # 17/4=4.25 and 17/6=2.833... truncate to 4 and 2 in the int slot; both undo to 17.
    ("divide_element", "2, 17", "int value", "a[1] /= value\na.get(1)", "4", 4, [2, 4], "6", 2, [2, 2]),
    # 17%4=1 and 17%6=5; replay restores the original 17 after either pair of slot records.
    ("remainder_element", "2, 17", "int value", "a[1] %= value\na.get(1)", "4", 1, [2, 1], "6", 5, [2, 5]),
    # Subtracting 4 or 6 from the saved 17 yields 13 or 11 without changing the first 2.
    ("subtract_element", "2, 17", "int value", "a[1] -= value\na.get(1)", "4", 13, [2, 13], "6", 11, [2, 11]),
    # Multiplying the saved 17 by 4 or 6 yields 68 or 102; rollback restores [2,17].
    ("multiply_element", "2, 17", "int value", "a[1] *= value\na.get(1)", "4", 68, [2, 68], "6", 102, [2, 102]),
]


def _cpp_vector(values, element_type="int"):
    return f"std::vector<{element_type}>{{" + ", ".join(json.dumps(value) for value in values) + "}"


def _assert_mutation_rollback(row, *, element_type="int", extra="", opt="-O2"):
    name, seed, signature, body, call, result, content, again, result2, content2 = row
    cpp = transpile(_native_source(seed, signature, body, call, extra=extra))
    _enrolled(cpp)
    original = f"std::vector<{element_type}>{{" + seed + "}"
    driver = f"""
#include <cassert>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    assert((strategy.a == {original}));
    strategy.snapshot_script_state();
    assert(strategy.mutate({call}) == {json.dumps(result)});
    assert((strategy.a == {_cpp_vector(content, element_type)}));
    strategy.restore_script_state();
    assert((strategy.a == {original}));
    assert(strategy._pf_array_journal.empty());
    strategy.restore_script_state();
    assert((strategy.a == {original}));
    assert(strategy.mutate({again}) == {json.dumps(result2)});
    assert((strategy.a == {_cpp_vector(content2, element_type)}));
    strategy.restore_script_state();
    assert((strategy.a == {original}));
    assert(strategy._pf_array_journal.empty());
    std::cout << "ok\\n";
}}
"""
    # Reverse replay recovers the literal seed after either speculative sequence.
    assert run_emitted_tu(cpp, driver, opt=opt, label=name) == "ok\n"


@pytest.mark.parametrize("row", MUTATIONS, ids=[row[0] for row in MUTATIONS])
def test_emitted_mutations_restore_twice_and_reuse_the_baseline(row):
    _assert_mutation_rollback(row)


@pytest.mark.parametrize("operator, records", [
    # Division spells the checked target as both the read and the assignment lvalue.
    ("/=", 2),
    # fmod likewise evaluates two checked targets, each saving the unchanged slot.
    ("%=", 2),
    # Native subtraction evaluates its checked lvalue once.
    ("-=", 1),
    # Native multiplication also has just one checked lvalue evaluation.
    ("*=", 1),
])
def test_legacy_compound_assignments_record_each_checked_target(operator, records):
    cpp = transpile(_native_source("2, 17", "int value", f"a[1] {operator} value\na.get(1)", "4"))
    _enrolled(cpp)
    assert cpp.count("this->_pf_array_journal.save_set(_pf_history_array, (size_t)_pf_history_offset)") == records


TYPED_MUTATIONS = [
    # Insert false at 1 or true at the end of [true,false], yielding three literal bits.
    ("bool_insert", "bool", "", "true, false", "int index, bool value", "a.insert(index, value)\na.size()", "1, false", 3, [True, False, False], "2, true", 3, [True, False, True]),
    # Prepending false or true keeps the original [true,false] suffix and returns size 3.
    ("bool_unshift", "bool", "", "true, false", "bool value", "a.unshift(value)\na.size()", "false", 3, [False, True, False], "true", 3, [True, True, False]),
    # Remove index 1's false or index 0's true by value, keeping the other two bits in order.
    ("bool_remove", "bool", "", "true, false, true", "int index", "a.remove(index)", "1", False, [True, True], "0", True, [False, True]),
    # Pop materializes the final false before shrinking [true,false] to [true].
    ("bool_pop", "bool", "", "true, false", "", "a.pop()", "", False, [True], "", False, [True]),
    # Shift materializes the initial true before moving the remaining false into slot 0.
    ("bool_shift", "bool", "", "true, false", "", "a.shift()", "", True, [False], "", True, [False]),
    # Reversing [true,false,false] yields [false,false,true], still of size 3.
    ("bool_reverse", "bool", "", "true, false, false", "", "a.reverse()\na.size()", "", 3, [False, False, True], "", 3, [False, False, True]),
    # Clear removes both strings and returns 0; replay must restore their original order.
    ("string_clear", "std::string", "", '"beta", "alpha"', "", "a.clear()\na.size()", "", 0, [], "", 0, []),
    # Filling [beta,alpha] with new or next replaces both strings and keeps size 2.
    ("string_fill", "std::string", "", '"beta", "alpha"', "string word", "a.fill(word)\na.size()", '"new"', 2, ["new", "new"], '"next"', 2, ["next", "next"]),
    # Byte order alpha < beta < gamma sorts [beta,alpha,gamma] to [alpha,beta,gamma], size 3.
    ("string_sort", "std::string", "", '"beta", "alpha", "gamma"', "", "a.sort()\na.size()", "", 3, ["alpha", "beta", "gamma"], "", 3, ["alpha", "beta", "gamma"]),
    # Concat appends the separate [tail,last] root to [beta,alpha], producing four words.
    ("string_concat", "std::string", 'var b = array.from("tail", "last")\n', '"beta", "alpha"', "", "a.concat(b)\na.size()", "", 4, ["beta", "alpha", "tail", "last"], "", 4, ["beta", "alpha", "tail", "last"]),
]


@pytest.mark.parametrize("row", TYPED_MUTATIONS, ids=[row[0] for row in TYPED_MUTATIONS])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_typed_mutations_restore_twice_and_reuse_the_baseline(row, opt):
    name, element_type, extra, *mutation = row
    _assert_mutation_rollback((name, *mutation), element_type=element_type, extra=extra, opt=opt)


def test_commit_adopts_the_array_and_next_bar_reset_releases_records():
    cpp = transpile(_native_source("2, 5", "int value", "a.push(value)\na.size()", "7"))
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
    strategy.mutate(7);
    strategy.commit_script_state();
    assert(strategy._pf_array_journal.empty());
    strategy.mutate(9);
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 5, 7}));
    strategy.snapshot_script_state();
    strategy.mutate(11);
    strategy.commit_script_state();
    assert(strategy._pf_array_journal.empty());
    strategy.mutate(13);
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 5, 7, 11}));
    strategy.mutate(17);
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    assert((strategy.a == std::vector<int>{2, 5}));
    assert(strategy._pf_array_journal.empty());
    std::cout << "ok\n";
}
'''
    # Commits retain 7 then 11; restores discard 9 then 13; a new run returns to [2,5].
    assert run_emitted_tu(cpp, driver, opt="-O2", label="array commit") == "ok\n"


def test_initializer_and_its_latch_restore_before_first_execution():
    cpp = transpile(_native_source("2, 5", "int value", "a.push(value)\na.size()", "7"))
    _enrolled(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.snapshot_script_state();
    strategy.on_source_bar(bar);
    assert((strategy.a == std::vector<int>{2, 5}));
    strategy.mutate(7);
    strategy.restore_script_state();
    assert(strategy.a.empty());
    assert(!strategy._var_initialized);
    strategy.restore_script_state();
    assert(strategy.a.empty());
    strategy.on_source_bar(bar);
    assert((strategy.a == std::vector<int>{2, 5}));
    strategy.restore_script_state();
    assert(strategy.a.empty());
    std::cout << "ok\n";
}
'''
    # The first initializer replaces []; rollback restores [] and its false once latch.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="array initialization") == "ok\n"


def test_concat_and_detached_slice_rollback():
    source = _native_source("2, 5", "", "a.concat(b)\na.size()", "",
                            extra="var b = array.from(7, 9)\n")
    source += "part = a.slice(0, 1)\npart.set(0, 99)\n"
    cpp = transpile(source)
    _enrolled(cpp)
    assert "this->_pf_array_journal.concat(a, a.end(), b.begin(), b.end())" in cpp
    assert "            b,\n" in _snapshot(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    assert((strategy.a == std::vector<int>{2, 5}));
    assert((strategy.part == std::vector<int>{99}));
    strategy.snapshot_script_state();
    assert(strategy.mutate() == 4);
    assert((strategy.a == std::vector<int>{2, 5, 7, 9}));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 5}));
    assert((strategy.b == std::vector<int>{7, 9}));
    assert((strategy.part == std::vector<int>{99}));
    std::cout << "ok\n";
}
'''
    # Concat appends [7,9]; the existing detached slice keeps [99] throughout rollback.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="concat and slice") == "ok\n"


def test_warmed_checkpoint_and_commit_allocate_no_array_payload():
    cpp = transpile(_native_source("2, 5", "int value", "a.push(value)\na.size()", "7"))
    driver = r'''
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
    strategy.mutate(7);
    observed_bytes = 0;
    observe_allocations = true;
    strategy.commit_script_state();
    observe_allocations = false;
    std::cout << snapshot_bytes << " " << observed_bytes << "\n";
}
'''
    # Two/three int payloads allocate on the eager path; tokens allocate zero bytes at either hook.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="checkpoint allocations") == "0 0\n"
    _enrolled(cpp)
    assert "this->_pf_array_journal.push(a, value)" in cpp


@pytest.mark.parametrize("values", [
    # Snapshot lengths 0+1+2+3 and commit lengths 1+2+3+4 formerly copied 16 ints.
    [2, 5, 7, 11],
    # Snapshot lengths 0..7 and commit lengths 1..8 formerly copied 64 ints.
    [2, 5, 7, 11, 13, 17, 19, 23],
])
def test_growing_checkpoint_payload_allocation_stays_zero(values):
    source = _native_source("2, 5", "int value", "a.push(value)\na.size()", "7").replace(
        "var a = array.from(2, 5)", "var a = array.new_int()")
    cpp = transpile(source)
    driver = r'''
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
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    assert(strategy.a.empty());
    const auto values = VALUES;
    observed_bytes = 0;
    for (int value : values) {
        observe_allocations = true;
        strategy.snapshot_script_state();
        observe_allocations = false;
        strategy.mutate(value);
        observe_allocations = true;
        strategy.commit_script_state();
        observe_allocations = false;
    }
    assert(strategy.a == values);
    strategy.restore_script_state();
    assert(strategy.a == values);
    std::cout << observed_bytes << "\n";
}
'''.replace("VALUES", _cpp_vector(values))
    # Address tokens copy no elements at either checkpoint, for both literal growth sequences.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="growing checkpoint allocations") == "0\n"
    _enrolled(cpp)


@pytest.mark.parametrize("choice", [0, 1])
def test_untyped_selection_initializer_keeps_eager_checkpoint(choice, monkeypatch):
    source = HEADER + f'''choice = input.int({choice}, "Choice")
var a = switch choice
    0 => array.from(2, 5)
    => array.from(7, 9)
a.push(11)
'''
    cpp = transpile(source)
    # Untyped array selections have no collection TypeSpec, regardless of the selected arm.
    assert "            a,\n" in _snapshot(cpp)
    assert "_pf_array_journal" not in cpp
    assert cpp == _eager_cpp(source, monkeypatch)


@pytest.mark.parametrize("initializer", [
    # The explicit default and the first case each replace the member once.
    pytest.param("switch selected\n    1 => array.from(2, 5)\n    => array.from(7, 9)\n", id="switch"),
    # Both if branches assign through the same declaration-site once guard.
    pytest.param("if selected == 1\n    array.from(2, 5)\nelse\n    array.from(7, 9)\n", id="if"),
])
@pytest.mark.parametrize("choice, content", [
    # Choice 0 computes selected=1, initializes [2,5], then appends 11.
    (0, [2, 5, 11]),
    # Choice 1 computes selected=2, initializes [7,9], then appends 11.
    (1, [7, 9, 11]),
])
def test_selection_initializer_restores_its_empty_member_and_latch(
    choice, content, initializer, monkeypatch,
):
    # The type hint proves array storage; the computed selector requires initialization in statement order.
    source = (HEADER + f'choice = input.int({choice}, "Choice")\n'
              + "selected = choice + 1\nvar array<int> a = " + initializer + "a.push(11)\n")
    cpp = transpile(source)
    _enrolled(cpp)
    # One typed member and one copied once latch serve both instrumented arms.
    assert "    std::vector<int> a;\n" in cpp
    assert "            _pf_var_init_a,\n" in _snapshot(cpp)
    assert cpp.count("if (!_pf_var_init_a)") == 1
    assert cpp.count("this->_pf_array_journal.replace(a,") == 2
    # Declaration order is a, the two ordinary globals, then the aggregate, declaration and input latches.
    eager = _eager_cpp(source, monkeypatch)
    assert _snapshot(eager) == (
        "        _pf_script_state_checkpoint_.emplace(_PFScriptState{\n"
        "            a,\n"
        "            choice,\n"
        "            selected,\n"
        "            _var_initialized,\n"
        "            _pf_var_init_a,\n"
        "            _inputs_initialized_,\n"
        "        });\n"
        "    }\n\n"
    )
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    // Before the first execution, the member is empty and neither initializer has run.
    assert(strategy.a.empty());
    assert(!strategy._var_initialized);
    assert(!strategy._pf_var_init_a);
    strategy.snapshot_script_state();
    strategy.on_source_bar(bar);
    assert((strategy.a == EXPECTED));
    assert(strategy._var_initialized);
    assert(strategy._pf_var_init_a);
    // Both restore calls recover the empty checkpoint and its false once latches.
    strategy.restore_script_state();
    assert(strategy.a.empty());
    assert(!strategy._var_initialized);
    assert(!strategy._pf_var_init_a);
    strategy.restore_script_state();
    assert(strategy.a.empty());
    assert(!strategy._var_initialized);
    assert(!strategy._pf_var_init_a);
    // Restoring the latch lets the same selection initialize again before the append.
    strategy.on_source_bar(bar);
    assert((strategy.a == EXPECTED));
    assert(strategy._var_initialized);
    assert(strategy._pf_var_init_a);
    strategy.restore_script_state();
    assert(strategy.a.empty());
    assert(!strategy._var_initialized);
    assert(!strategy._pf_var_init_a);
    std::cout << "ok\n";
}
'''.replace("EXPECTED", _cpp_vector(content))
    # Undo the append before restoring the initializer's empty preimage and once latch.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="selected array initializer") == "ok\n"
    # An eager payload checkpoint must restore the same empty member and false latches.
    assert run_emitted_tu(eager, driver, opt="-O2", label="eager selected array initializer") == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_float_preimages_preserve_signed_zero_and_nan_payload(opt):
    cpp = transpile(_native_source(
        "0.0, 0.0", "float replacement", "a.reverse()\na.set(1, replacement)\na.size()", "9.0"))
    _enrolled(cpp)
    driver = r'''
#include <cassert>
#include <cstring>
#include <iostream>
double from_bits(uint64_t bits) { double value; std::memcpy(&value, &bits, 8); return value; }
uint64_t bits_of(double value) { uint64_t bits; std::memcpy(&bits, &value, 8); return bits; }
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.a[0] = from_bits(0x8000000000000000ULL);
    strategy.a[1] = from_bits(0x7ff8000000000042ULL);
    strategy.snapshot_script_state();
    assert(strategy.mutate(9.0) == 2);
    assert(bits_of(strategy.a[0]) == 0x7ff8000000000042ULL);
    assert(strategy.a[1] == 9.0);
    strategy.restore_script_state();
    assert(bits_of(strategy.a[0]) == 0x8000000000000000ULL);
    assert(bits_of(strategy.a[1]) == 0x7ff8000000000042ULL);
    strategy.restore_script_state();
    assert(strategy.mutate(7.0) == 2);
    assert(strategy.a[1] == 7.0);
    strategy.restore_script_state();
    assert(bits_of(strategy.a[0]) == 0x8000000000000000ULL);
    assert(bits_of(strategy.a[1]) == 0x7ff8000000000042ULL);
    std::cout << "ok\n";
}
'''
    # Reverse saves both original bit patterns; set saves -0 without computing an inverse.
    assert run_emitted_tu(cpp, driver, opt=opt, label="floating preimages") == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_bool_string_and_wide_integer_preimages(opt):
    source = HEADER + r'''var flags = array.from(true, false)
var words = array.from("old", "tail")
var stamps = array.new_int(2, 0)
var array<color> shades = array.new<color>(2, color.red)
mutate(bool bit, string word, int number, color shade) =>
    flags.set(0, bit)
    flags.push(bit)
    words.set(0, word)
    words.set(1, word)
    words.push(word)
    stamps.set(0, number)
    stamps.set(1, number + 1)
    stamps.push(time)
    shades.set(0, shade)
    shades.push(shade)
    words.size()
armed = input.bool(false)
if armed
    mutate(false, "new", 13, color.blue)
'''
    cpp = transpile(source)
    for name in ("flags", "words", "stamps", "shades"):
        _enrolled(cpp, name)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.words[0] = std::string("a\0b", 3);
    strategy.stamps[0] = 9007199254740993LL;
    strategy.stamps[1] = na<int64_t>();
    strategy.shades[0] = 0x11223344LL;
    strategy.shades[1] = 0x55667788LL;
    strategy.snapshot_script_state();
    assert(strategy.mutate(false, std::string("n\0w", 3), 13, 0x12345678LL) == 3);
    assert((strategy.flags == std::vector<bool>{false, false, false}));
    assert(strategy.words[0] == std::string("n\0w", 3));
    assert(strategy.words[1] == std::string("n\0w", 3));
    assert(strategy.words[2] == std::string("n\0w", 3));
    assert(strategy.stamps[0] == 13 && strategy.stamps[1] == 14 && strategy.stamps.size() == 3);
    assert(strategy.shades[0] == 0x12345678LL && strategy.shades[2] == 0x12345678LL);
    strategy.restore_script_state();
    assert((strategy.flags == std::vector<bool>{true, false}));
    assert(strategy.words.size() == 2);
    assert(strategy.words[0] == std::string("a\0b", 3) && strategy.words[1] == "tail");
    assert(strategy.stamps.size() == 2);
    assert(strategy.stamps[0] == 9007199254740993LL && is_na(strategy.stamps[1]));
    assert((strategy.shades == std::vector<int64_t>{0x11223344LL, 0x55667788LL}));
    strategy.mutate(true, "again", 17, 0x76543210LL);
    assert((strategy.flags == std::vector<bool>{true, false, true}));
    assert(strategy.words[0] == "again" && strategy.words[1] == "again");
    assert(strategy.stamps[0] == 17 && strategy.stamps[1] == 18);
    assert(strategy.shades[0] == 0x76543210LL);
    strategy.restore_script_state();
    assert((strategy.flags == std::vector<bool>{true, false}));
    assert(strategy.words[0] == std::string("a\0b", 3) && strategy.words[1] == "tail");
    assert(strategy.stamps[0] == 9007199254740993LL && is_na(strategy.stamps[1]));
    assert((strategy.shades == std::vector<int64_t>{0x11223344LL, 0x55667788LL}));
    std::cout << "ok\n";
}
'''
    # Typed preimages retain true, all three NUL-containing bytes, the wide integer, na, and colors.
    assert run_emitted_tu(cpp, driver, opt=opt, label="primitive preimages") == "ok\n"


def test_string_insertions_restore_full_preimages_in_reverse_order():
    cpp = transpile(_native_source(
        '"old", "tail"', "string word", "a.insert(1, word)\na.unshift(word)\na.size()", '"new"'))
    _enrolled(cpp)
    assert "if constexpr (std::is_same_v<T, std::string>)" in cpp
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.a[0] = std::string("a\0b", 3);
    strategy.snapshot_script_state();
    assert(strategy.mutate("new") == 4);
    assert((strategy.a == std::vector<std::string>{"new", std::string("a\0b", 3), "new", "tail"}));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<std::string>{std::string("a\0b", 3), "tail"}));
    strategy.restore_script_state();
    assert(strategy.mutate("next") == 4);
    assert((strategy.a == std::vector<std::string>{"next", std::string("a\0b", 3), "next", "tail"}));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<std::string>{std::string("a\0b", 3), "tail"}));
    assert(strategy._pf_array_journal.empty());
    std::cout << "ok\n";
}
'''
    # Undo the prepend's three-element preimage, then the insertion's original two strings.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="string insert preimages") == "ok\n"


@pytest.mark.parametrize("operation, seed, content, message", [
    # Pop on [] stops before any removal or journal entry.
    ("a.pop()", "array.new_int()", [], "Cannot use pop() if array is empty."),
    # Shift on [] stops before any removal or journal entry.
    ("a.shift()", "array.new_int()", [], "Cannot use shift() if array is empty."),
    # Index 2 lies just past [2,5], so both elements remain unchanged.
    ("a.set(2, 9)", "array.from(2, 5)", [2, 5], "Index 2 is out of bounds. Array size is 2"),
    # A typed na index is rejected before saving or replacing an element.
    ("a.set(int(na), 9)", "array.from(2, 5)", [2, 5], "Index na is out of bounds. Array size is 2"),
    # Insertion may append at 2, but index 3 exceeds the size-2 insertion boundary.
    ("a.insert(3, 9)", "array.from(2, 5)", [2, 5], "Index 3 is out of bounds. Array size is 2"),
    # End-relative -3 normalizes to -1 for size 2 and fails before an insertion record.
    ("a.insert(-3, 9)", "array.from(2, 5)", [2, 5], "Index -3 is out of bounds. Array size is 2"),
    # An na insertion index stops before the journal receives any edit.
    ("a.insert(int(na), 9)", "array.from(2, 5)", [2, 5], "Index na is out of bounds. Array size is 2"),
    # Removal needs an existing slot, so index 2 is outside [2,5].
    ("a.remove(2)", "array.from(2, 5)", [2, 5], "Index 2 is out of bounds. Array size is 2"),
    # End-relative -3 again normalizes below zero and preserves both elements.
    ("a.remove(-3)", "array.from(2, 5)", [2, 5], "Index -3 is out of bounds. Array size is 2"),
    # An na removal index stops before saving or erasing either element.
    ("a.remove(int(na))", "array.from(2, 5)", [2, 5], "Index na is out of bounds. Array size is 2"),
])
def test_rejected_mutations_leave_no_undo_record(operation, seed, content, message):
    source = (HEADER + f"var a = {seed}\nmutate() =>\n    {operation}\n    0\n"
              + "armed = input.bool(false)\nif armed\n    mutate()\n")
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
    const auto original = INITIAL;
    assert(strategy.a == original);
    strategy.snapshot_script_state();
    try { strategy.mutate(); assert(false); }
    catch (const std::exception& error) { assert(std::string(error.what()) == EXPECTED); }
    assert(strategy._pf_array_journal.empty());
    assert(strategy.a == original);
    strategy.restore_script_state();
    assert(strategy.a == original);
    std::cout << "ok\n";
}
'''.replace("EXPECTED", json.dumps(message)).replace("INITIAL", _cpp_vector(content))
    assert run_emitted_tu(cpp, driver, opt="-O2", label="checked array stops") == "ok\n"


def test_argument_side_effects_precede_checks_and_rollback_across_roots():
    source = HEADER + '''var a = array.from(2, 5)
var b = array.from(7, 11)
operand(bool stop) =>
    b.push(13)
    if stop
        runtime.error("operand stopped")
    9
mutate(int index, bool stop) =>
    a.set(index, operand(stop))
    a.size()
armed = input.bool(false)
if armed
    mutate(1, false)
'''
    cpp = transpile(source)
    _enrolled(cpp)
    _enrolled(cpp, "b")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    try { strategy.mutate(2, false); assert(false); }
    catch (const std::exception& error) {
        assert(std::string(error.what()) == "Index 2 is out of bounds. Array size is 2");
    }
    assert((strategy.a == std::vector<int>{2, 5}));
    assert((strategy.b == std::vector<int>{7, 11, 13}));
    strategy.restore_script_state();
    assert((strategy.b == std::vector<int>{7, 11}));
    try { strategy.mutate(1, true); assert(false); }
    catch (const std::exception& error) { assert(std::string(error.what()) == "operand stopped"); }
    assert((strategy.a == std::vector<int>{2, 5}));
    assert((strategy.b == std::vector<int>{7, 11, 13}));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 5}));
    assert((strategy.b == std::vector<int>{7, 11}));
    assert(strategy._pf_array_journal.empty());
    assert(strategy.mutate(0, false) == 2);
    assert((strategy.a == std::vector<int>{9, 5}));
    assert((strategy.b == std::vector<int>{7, 11, 13}));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 5}));
    assert((strategy.b == std::vector<int>{7, 11}));
    std::cout << "ok\n";
}
'''
    # The operand appends 13 before either stop; only a valid set overwrites a, and both roots undo.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="array argument ordering") == "ok\n"


@pytest.mark.parametrize("operation, content, error", [
    # Pop removes 5 before set checks index 0; only 5 remains until rollback.
    ("a.set(index, a.pop())", [5], "Index 1 is out of bounds. Array size is 1"),
    # C++ assignment evaluates the pop RHS before checking the legacy lvalue.
    ("a[index] := a.pop()", [5], "Array history element index is out of bounds."),
    # Compound assignment also evaluates its RHS first, then adds 5 to the remaining 2.
    ("a[index] += a.pop()", [7], "Array history element index is out of bounds."),
])
def test_same_array_argument_writes_undo_before_target_preimages(operation, content, error):
    cpp = transpile(_native_source("2, 5", "int index", operation + "\na.size()", "0"))
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
    assert(strategy.mutate(0) == 1);
    assert((strategy.a == EXPECTED));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 5}));
    try { strategy.mutate(1); assert(false); }
    catch (const std::exception& exception) { assert(std::string(exception.what()) == ERROR); }
    assert((strategy.a == std::vector<int>{2}));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{2, 5}));
    std::cout << "ok\n";
}
'''.replace("EXPECTED", _cpp_vector(content)).replace("ERROR", json.dumps(error))
    # Restore undoes the final slot write before reinserting the popped 5; a failed check only undoes pop.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="same array operands") == "ok\n"


def test_every_callable_instance_journals_its_direct_global_writes():
    source = HEADER + '''var a = array.new_int()
step(int value) =>
    var int count = 0
    count += 1
    a.push(value + count)
    a.size()
armed = input.bool(false)
if armed
    step(2)
    step(5)
'''
    cpp = transpile(source)
    _enrolled(cpp)
    assert cpp.count("this->_pf_array_journal.push(a,") == 2  # Both written calls own a helper instance.
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    strategy.snapshot_script_state();
    strategy.step_cs0(2);
    strategy.step_cs1(5);
    assert((strategy.a == std::vector<int>{3, 6}));
    strategy.restore_script_state();
    assert(strategy.a.empty());
    strategy.step_cs1(9);
    strategy.step_cs0(7);
    assert((strategy.a == std::vector<int>{10, 8}));
    strategy.restore_script_state();
    assert(strategy.a.empty());
    strategy.step_cs0(2);
    strategy.commit_script_state();
    strategy.step_cs0(2);
    assert((strategy.a == std::vector<int>{3, 4}));
    strategy.restore_script_state();
    assert((strategy.a == std::vector<int>{3}));
    std::cout << "ok\n";
}
'''
    # Independent counters first add 1; after commit the first counter adds 2, then rolls back to 1.
    assert run_emitted_tu(cpp, driver, opt="-O2", label="global array callable instances") == "ok\n"


COLLECTION_TRACE_NAMES = (
    "append_size", "append_last", "set_first", "removed", "remaining",
    "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7",
)


def _scheduled_source(units, eager=False):
    source = schedule._source(units).replace("callbacks += 1\n", '''callbacks += 1
var a = array.new_int()
var increments = array.from(2, 5)
var removals = array.from(2, 5, 7, 11, 13, 17, 19, 23)
a.push(chartWalks)
increments.set(0, increments.get(0) + 1)
removed = removals.remove(0)
''')
    if eager:
        # A harmless array argument escapes the direct-root proof, restoring eager snapshots.
        source += '''observe(array<int> values) => values.size()
observed = observe(a) + observe(increments) + observe(removals)
'''
    source += '''// @pf-trace append_size=a.size()
// @pf-trace append_last=a.last()
// @pf-trace set_first=increments.first()
// @pf-trace removed=removed
// @pf-trace remaining=removals.size()
'''
    for index in range(8):
        source += f"// @pf-trace a{index}=a.size() > {index} ? a.get({index}) : -1\n"
    return source


def _scheduled_run(engine, base, feed, aux, key, units, eager=False):
    work = base / key
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
    return ok(execute_all(engine, feed, base, {
        key: Build(_scheduled_source(units, eager), trace=True),
    }), key)


def _scheduled_frames(records, chart):
    names = schedule.TRACE_NAMES + COLLECTION_TRACE_NAMES
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


# One ordinary committed append, increment and front removal per chart bar.
SCHEDULED_VALUES = [
    # Step 1 appends 1, increments 2 to 3, and removes 2, leaving seven values.
    (1, 1, 3, 2, 7, [1, -1, -1, -1, -1, -1, -1, -1]),
    # Step 2 appends 2, increments to 4, and removes 5, leaving six values.
    (2, 2, 4, 5, 6, [1, 2, -1, -1, -1, -1, -1, -1]),
    # Step 3 appends 3, increments to 5, and removes 7, leaving five values.
    (3, 3, 5, 7, 5, [1, 2, 3, -1, -1, -1, -1, -1]),
    # Step 4 appends 4, increments to 6, and removes 11, leaving four values.
    (4, 4, 6, 11, 4, [1, 2, 3, 4, -1, -1, -1, -1]),
    # Step 5 appends 5, increments to 7, and removes 13, leaving three values.
    (5, 5, 7, 13, 3, [1, 2, 3, 4, 5, -1, -1, -1]),
    # Step 6 appends 6, increments to 8, and removes 17, leaving two values.
    (6, 6, 8, 17, 2, [1, 2, 3, 4, 5, 6, -1, -1]),
    # Step 7 appends 7, increments to 9, and removes 19, leaving one value.
    (7, 7, 9, 19, 1, [1, 2, 3, 4, 5, 6, 7, -1]),
    # Step 8 appends 8, increments to 10, and removes 23, leaving an empty array.
    (8, 8, 10, 23, 0, [1, 2, 3, 4, 5, 6, 7, 8]),
]


def _assert_scheduled_values(row, expected):
    assert tuple(row[name] for name in COLLECTION_TRACE_NAMES[:5]) == expected[:5]
    assert [row[f"a{index}"] for index in range(8)] == expected[5]


def test_growing_arrays_match_eager_execution_under_real_fill_callbacks(tmp_path):
    engine = skip_unless_e2e_env()
    feed, aux, chart, _minutes = schedule._feeds(tmp_path)
    cpp = transpile(_scheduled_source(3))
    for name in ("a", "increments", "removals"):
        _enrolled(cpp, name)
    assert "_pf_array_journal" not in transpile(_scheduled_source(3, eager=True))

    control = _scheduled_run(engine, tmp_path, feed, aux, "control", 2)
    control_rows = _scheduled_frames(control.traces["default"], chart)
    # The 30m request advances every two chart bars; 5m's first bucket advances by three.
    higher = [0, 1, 1, 2, 2, 3, 3, 4]
    finer = [1, 4, 7, 10, 13, 16, 19, 22]
    # Two funded units never trigger the rounded full-margin event.
    positions = [0, 2, 2, 2, 2, 2, 2, 2]
    walks = [1, 2, 3, 4, 5, 6, 7, 8]
    for index, bar in enumerate(chart):
        for row in control_rows[bar[0]]:
            _assert_scheduled_values(row, SCHEDULED_VALUES[index])
            assert (row["higher"], row["finer"]) == (higher[index], finer[index])
            assert row["chart_walks"] == walks[index]
            assert row["position"] == positions[index]
            assert row["closed"] == row["margin"] == row["margin_price"] == 0
    assert len(control_rows[chart[3][0]]) == 1  # Bar 3 has no fill in the funded control.

    # All control assertions precede either full-margin build.
    eager = _scheduled_run(engine, tmp_path, feed, aux, "eager", 3, eager=True)
    journal = _scheduled_run(engine, tmp_path, feed, aux, "journal", 3)
    assert journal.trades == eager.trades
    assert journal.traces == eager.traces
    rows = _scheduled_frames(journal.traces["default"], chart)
    late = rows[chart[3][0]]
    # Three full-margin units lose one unit only on the late bar-3 callback.
    assert [(row["position"], row["closed"], row["margin"]) for row in late] == [(3, 0, 0), (2, 1, 1)]
    _assert_scheduled_values(late[0], SCHEDULED_VALUES[3])
    _assert_scheduled_values(late[1], SCHEDULED_VALUES[4])
    # The extra body sees walk 5; the following bar repeats that fifth mutation after restoring.
    assert [row["chart_walks"] for row in late] == [4, 5]
    assert late[1]["callbacks"] - late[0]["callbacks"] == 1
    assert rows[chart[4][0]][0]["callbacks"] - late[1]["callbacks"] == 1
    assert late[1]["margin_price"] == 100_000_000.25
    # Only the margin fill adds a callback; none are collapsed by the trace decoder.
    assert sum(map(len, rows.values())) - sum(map(len, control_rows.values())) == 1
    # The late fill closes one unit; two units and one closed trade persist afterward.
    positions = [0, 3, 3, 3, 2, 2, 2, 2]
    closed = [0, 0, 0, 0, 1, 1, 1, 1]
    for index, bar in enumerate(chart):
        for execution, row in enumerate(rows[bar[0]]):
            is_late = index == 3 and execution == 1
            expected = SCHEDULED_VALUES[4] if is_late else SCHEDULED_VALUES[index]
            _assert_scheduled_values(row, expected)
            assert (row["higher"], row["finer"]) == (higher[index], finer[index])
            assert row["position"] == (2 if is_late else positions[index])
            assert row["closed"] == row["margin"] == (1 if is_late else closed[index])
    # Eight committed chart steps retain the literal sequence [1,2,3,4,5,6,7,8].
    assert [rows[chart[7][0]][-1][f"a{i}"] for i in range(8)] == [1, 2, 3, 4, 5, 6, 7, 8]
