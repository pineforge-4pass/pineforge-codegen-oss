"""Lane V: the scratch-facts classifier is a safety property, not a guess.

The truth table below is hand-written from the Pine source: every expected
classification and reason is a one-line human judgement, never a value read
back from the emitter under test. The AST mutants start from an IN program and
replace one node kind with a dynamic form; each must classify OUT.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import (
    ASTNode,
    BinOp,
    ExprStmt,
    ForStmt,
    FuncCall,
    FuncDef,
    Identifier,
    MemberAccess,
    NumberLiteral,
    Program,
    StringLiteral,
    Subscript,
    VarDecl,
)
from pineforge_codegen.codegen.scratch_facts import (
    SCRATCH_FACT_SETS,
    analyze_program,
    classify_program,
    encode_certificate,
)


DECLARATION = '//@version=6\nstrategy("scratch facts")\n'
RECEIPT = re.compile(r'checked_settings::receipt\(("(?:[^"\\]|\\.)*"), json, capacity, required\);')


def scratch_facts(body: str, *, check_support: bool = False) -> dict:
    """The additive scratch_facts object from one emitted order-shapes receipt."""

    cpp = transpile(DECLARATION + body, check_support=check_support)
    documents = RECEIPT.findall(cpp)
    assert len(documents) == 3
    return json.loads(json.loads(documents[2]))["scratch_facts"]


# (name, Pine body, expected classification, reason codes that must be present)
TRUTH_TABLE = [
    # --- IN: a finite, complete vocabulary ---------------------------------
    ("literal_entry",
     'strategy.entry("L", strategy.long)', "in_subset", ()),
    ("literal_order",
     'strategy.order("O", strategy.long)', "in_subset", ()),
    ("literal_exit_named",
     'strategy.entry("L", strategy.long)\nstrategy.exit("X", "L", limit=close)',
     "in_subset", ()),
    ("literal_exit_global",
     'strategy.exit("X", limit=close)', "in_subset", ()),
    ("literal_close",
     'strategy.entry("L", strategy.long)\nstrategy.close("L")', "in_subset", ()),
    ("literal_cancel",
     'strategy.entry("L", strategy.long)\nstrategy.cancel("L")', "in_subset", ()),
    ("ternary_of_literals_bound",
     'id = close > open ? "A" : "B"\nstrategy.entry(id, strategy.long)',
     "in_subset", ()),
    ("ternary_of_literals_argument",
     'strategy.entry(close > open ? "A" : "B", strategy.long)',
     "in_subset", ()),
    ("nested_ternary",
     'id = close > open ? (high > low ? "A" : "B") : "C"\nstrategy.entry(id, strategy.long)',
     "in_subset", ()),
    ("input_string_bound",
     'id = input.string("L")\nstrategy.entry(id, strategy.long)',
     "in_subset", ()),
    ("input_string_argument",
     'strategy.entry(input.string("L"), strategy.long)',
     "in_subset", ()),
    ("bound_literal",
     'id = "L"\nstrategy.entry(id, strategy.long)', "in_subset", ()),
    ("if_else_literals",
     'id = "A"\nif close > open\n    id := "B"\nstrategy.entry(id, strategy.long)',
     "in_subset", ()),
    ("same_id_in_for_loop",
     'for i = 0 to 2\n    strategy.entry("L", strategy.long)',
     "in_subset", ()),
    ("same_id_in_while_loop",
     'int i = 0\nwhile i < 3\n    strategy.entry("L", strategy.long)\n    i += 1',
     "in_subset", ()),
    ("same_id_two_sites",
     'strategy.entry("L", strategy.long)\nstrategy.entry("L", strategy.long)',
     "in_subset", ()),
    ("no_order_calls",
     'value = close', "in_subset", ()),
    ("user_function_identity",
     'f(x) => x\nstrategy.entry(f("L"), strategy.long)', "in_subset", ()),
    ("user_function_two_constants",
     'f(x) => x\nstrategy.entry(f("A"), strategy.long)\nstrategy.entry(f("B"), strategy.long)',
     "in_subset", ()),
    ("two_inputs",
     'a = input.string("A")\nb = input.string("B")\n'
     'strategy.entry(a, strategy.long)\nstrategy.entry(b, strategy.long)',
     "in_subset", ()),
    ("switch_of_literals",
     'id = switch close > open\n    true => "A"\n    => "B"\n'
     'strategy.entry(id, strategy.long)',
     "in_subset", ()),

    # --- OUT: one dynamic form each, with the reason it is unrecoverable ---
    ("str_tostring_of_series",
     'strategy.entry(str.tostring(bar_index), strategy.long)',
     "out_of_subset", ("str_tostring", "series_value")),
    ("growing_string_concat",
     'id = "L" + str.tostring(close)\nstrategy.entry(id, strategy.long)',
     "out_of_subset", ("string_concat", "str_tostring")),
    ("series_history_id",
     'strategy.entry(str.tostring(close[1]), strategy.long)',
     "out_of_subset", ("str_tostring", "subscript")),
    ("array_get_id",
     'array<string> ids = array.from("A", "B")\n'
     'strategy.entry(array.get(ids, 0), strategy.long)',
     "out_of_subset", ("array_read",)),
    ("loop_counter_id",
     'for i = 1 to 3\n    strategy.entry(str.tostring(i), strategy.long)',
     "out_of_subset", ("str_tostring", "loop_variable")),
    ("user_function_nonconstant",
     'f() =>\n    x = str.tostring(bar_index)\n    x\n'
     'strategy.entry(f(), strategy.long)',
     "out_of_subset", ("user_function_nonconstant",)),
    ("user_function_dynamic_argument",
     'f(x) => x\nstrategy.entry(f(str.tostring(bar_index)), strategy.long)',
     "out_of_subset", ("str_tostring", "series_value")),
    ("request_security_id",
     'h = request.security(syminfo.tickerid, "D", close)\n'
     'strategy.entry(str.tostring(h), strategy.long)',
     "out_of_subset", ("request_security", "str_tostring")),
    ("growing_var_concat",
     'var string id = "L"\nif close > open\n    id := id + str.tostring(bar_index)\n'
     'strategy.entry(id, strategy.long)',
     "out_of_subset", ("self_reference", "string_concat")),
    ("switch_with_dynamic_arm",
     'switch close > open\n'
     '    true => strategy.entry("A", strategy.long)\n'
     '    => strategy.entry(str.tostring(bar_index), strategy.long)',
     "out_of_subset", ("str_tostring", "series_value")),
    ("dynamic_from_entry",
     'strategy.exit("X", str.tostring(bar_index), limit=close)',
     "out_of_subset", ("str_tostring", "series_value")),
    ("dynamic_close_id",
     'strategy.close(str.tostring(bar_index))',
     "out_of_subset", ("str_tostring", "series_value")),
    ("member_access_id",
     'strategy.entry(strategy.opentrades.entry_id(0), strategy.long)',
     "out_of_subset", ("method_call",)),
]


@pytest.mark.parametrize(("name", "body", "expected", "reasons"),
                         TRUTH_TABLE, ids=[row[0] for row in TRUTH_TABLE])
def test_truth_table(name, body, expected, reasons):
    facts = scratch_facts(body)
    assert facts["version"] == 1
    assert set(facts) == {"version", "classification", "certificate", "reasons"}
    assert facts["classification"] == expected, (name, facts)
    assert set(reasons) <= set(facts["reasons"]), (name, facts["reasons"])
    if expected == "in_subset":
        assert facts["reasons"] == [], name
    # The certificate is lowercase hex of whole bytes.
    assert re.fullmatch(r"(?:[0-9a-f]{2})*", facts["certificate"]), name


def test_certificate_carries_the_pine_values():
    facts = scratch_facts('strategy.entry("L", strategy.long)\nstrategy.close("L")')
    # Decode the wire independently: version, then S,F,E,H,J as
    # (complete byte, u64 count, per entry a u64 length and raw bytes).
    wire = bytes.fromhex(facts["certificate"])
    assert wire[0] == 1
    offset = 1
    sets = {}
    for name in SCRATCH_FACT_SETS:
        complete = wire[offset]
        offset += 1
        count = int.from_bytes(wire[offset:offset + 8], "little")
        offset += 8
        entries = []
        for _ in range(count):
            length = int.from_bytes(wire[offset:offset + 8], "little")
            offset += 8
            entries.append(wire[offset:offset + length].decode("utf-8"))
            offset += length
        sets[name] = (complete, entries)
    assert offset == len(wire)
    # The Pine ids are present; the engine's closed labels are added to S.
    assert "L" in sets["S"][1] and "X" not in sets["S"][1]
    assert "L" in sets["E"][1]
    # "" is the global from_entry; a close may also copy its own ID there.
    assert sets["F"] == (1, ["", "L"])
    assert "L" in sets["J"][1]
    assert "__margin_call__" in sets["S"][1]
    assert "__margin_preopen__L" in sets["S"][1]
    assert all(complete == 1 for complete, _entries in sets.values())


# --- mutants -------------------------------------------------------------

@dataclass
class UnknownExpr(ASTNode):
    """A node kind the walker does not know: UNKNOWN IS OUT."""

    label: str = "L"


def long_direction():
    return MemberAccess(object=Identifier(name="strategy"), member="long")


def strategy_call(name, args=(), kwargs=None):
    return FuncCall(callee=MemberAccess(object=Identifier(name="strategy"), member=name),
                    args=list(args), kwargs=dict(kwargs or {}))


def entry_statement(id_node):
    return ExprStmt(expr=strategy_call("entry", [id_node, long_direction()]))


def facts_of(*statements):
    return classify_program(Program(body=list(statements)))


def test_identifier_mutant_is_out():
    facts = facts_of(VarDecl(name="id", value=StringLiteral(value="L")),
                     entry_statement(Identifier(name="id")))
    assert facts["classification"] == "in_subset"
    assert facts["reasons"] == []


MUTANTS = [
    ("str_tostring", ("str_tostring", "series_value"),
     lambda: FuncCall(callee=MemberAccess(object=Identifier(name="str"), member="tostring"),
                      args=[Identifier(name="bar_index")])),
    ("string_concat", ("string_concat", "str_tostring"),
     lambda: BinOp(left=StringLiteral(value="L"), op="+",
                   right=FuncCall(callee=MemberAccess(object=Identifier(name="str"),
                                                      member="tostring"),
                                  args=[Identifier(name="bar_index")]))),
    ("array_read", ("array_read",),
     lambda: FuncCall(callee=MemberAccess(object=Identifier(name="array"), member="get"),
                      args=[Identifier(name="ids"), NumberLiteral(value=0)])),
    ("series_value", ("series_value",), lambda: Identifier(name="bar_index")),
    ("subscript", ("subscript",),
     lambda: Subscript(object=Identifier(name="ids"), index=NumberLiteral(value=0))),
    ("member_access", ("member_access",),
     lambda: MemberAccess(object=Identifier(name="strategy"), member="opentrades")),
    ("request_security", ("request_security",),
     lambda: FuncCall(callee=MemberAccess(object=Identifier(name="request"),
                                          member="security"),
                      args=[Identifier(name="syminfo"), StringLiteral(value="D"),
                            Identifier(name="close")])),
    ("unrecognized_call", ("unrecognized_call",),
     lambda: FuncCall(callee=Identifier(name="mystery"), args=[])),
    ("unknown_node", ("unrecognized_expression",), lambda: UnknownExpr()),
]


@pytest.mark.parametrize(("name", "reasons", "node"), MUTANTS, ids=[row[0] for row in MUTANTS])
def test_mutant_node_kind_is_out(name, reasons, node):
    facts = facts_of(entry_statement(node()))
    assert facts["classification"] == "out_of_subset", (name, facts)
    assert set(reasons) <= set(facts["reasons"]), (name, facts["reasons"])


def test_mutant_loop_variable_is_out():
    body = [ForStmt(var="i", start=NumberLiteral(value=1), end=NumberLiteral(value=3),
                    body=[entry_statement(Identifier(name="i"))])]
    facts = classify_program(Program(body=body))
    assert facts["classification"] == "out_of_subset"
    assert "loop_variable" in facts["reasons"]


def test_mutant_unknown_node_in_from_entry_is_out():
    call = strategy_call("exit", [StringLiteral(value="X"), UnknownExpr()],
                         {"limit": Identifier(name="close")})
    facts = facts_of(ExprStmt(expr=call))
    assert facts["classification"] == "out_of_subset"
    assert "unrecognized_expression" in facts["reasons"]


def test_mutant_recursive_user_function_is_out():
    function = FuncDef(name="f", params=["x"], is_single_expr=True,
                       body=[ExprStmt(expr=FuncCall(callee=Identifier(name="f"),
                                                    args=[Identifier(name="x")]))])
    facts = facts_of(function, entry_statement(FuncCall(callee=Identifier(name="f"),
                                                        args=[StringLiteral(value="L")])))
    assert facts["classification"] == "out_of_subset"
    assert "recursive_call" in facts["reasons"]


# --- determinism ---------------------------------------------------------

DETERMINISM_BODY = ('a = input.string("A")\nb = input.string("B")\n'
                    'strategy.entry(a, strategy.long)\nstrategy.entry(b, strategy.long)\n')
REVERSED_BODY = ('b = input.string("B")\na = input.string("A")\n'
                 'strategy.entry(a, strategy.long)\nstrategy.entry(b, strategy.long)\n')


def test_same_program_emits_the_same_certificate_twice():
    first = scratch_facts(DETERMINISM_BODY)["certificate"]
    second = scratch_facts(DETERMINISM_BODY)["certificate"]
    assert first == second


def test_declaration_order_does_not_change_the_certificate():
    first = scratch_facts(DETERMINISM_BODY)["certificate"]
    reversed_declarations = scratch_facts(REVERSED_BODY)["certificate"]
    assert first == reversed_declarations


def test_encoder_is_independent_of_iteration_order_and_duplicates():
    rosters = [(True, ["b", "a", "b"]), (True, [""]), (False, ["x"]), (True, ["", "L"]), (True, ["L"])]
    shuffled = [(complete, list(reversed(strings)) + strings[:1])
                for complete, strings in rosters]
    expected = encode_certificate(rosters)
    assert encode_certificate(shuffled) == expected
    # The set iteration order of a real Python set is irrelevant too.
    as_sets = [(complete, set(strings)) for complete, strings in rosters]
    assert encode_certificate(as_sets) == expected


# --- cross-language golden ----------------------------------------------

GOLDEN = Path(__file__).parent / "data" / "scratch_receipt_golden.json"
VECTORS = json.loads(GOLDEN.read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["name"] for vector in VECTORS])
def test_hand_computed_golden(vector):
    # Wire arithmetic, from docs/design/scratch-facts.md:50-62. Each set costs
    # 1 complete byte + 8 count bytes + per entry (8 length bytes + its bytes).
    #   doc_worked_vector   1 + (1+8+8+2) + (1+8+8+0) + (1+8+8+1) + (1+8) + (1+8+8+1) = 82
    #   incomplete_exit     1 + 18 + 17 + 18 + (1+8+8+8+1) + 18                          = 98
    #   sorted/NUL vector   1 + (1+8+16+2) + 9 + (1+8+8+3) + 9 + (1+8+16+2)            = 93
    # The layout is the hand-typed byte list of that format; the certificate
    # string is a second, independent transcription of it.
    assert vector["certificate"] == "".join(vector["layout"])
    rosters = [(entry["complete"], entry["strings"]) for entry in vector["sets"]]
    assert encode_certificate(rosters).hex() == vector["certificate"]
    # Reversing every roster proves the encoder sorts instead of trusting order.
    reversed_rosters = [(complete, list(reversed(strings))) for complete, strings in rosters]
    assert encode_certificate(reversed_rosters).hex() == vector["certificate"]


def test_analyze_program_exposes_every_roster():
    facts = analyze_program(Program(body=[entry_statement(StringLiteral(value="L"))]))
    assert set(facts) == set(SCRATCH_FACT_SETS)
    assert facts["S"].complete and facts["J"].complete
    assert facts["J"].values == frozenset(("L",))
