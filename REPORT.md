# Fix round 1

CR-221 scope A, lane V, codegen half. EDIT ONLY; supervisor review and lab
re-proof remain pending. No staging, commit, transpilation, build, test, or
repository script was run.

Read all 642 lines of
`../lab-fail-rj-20261021t043030-a526b7/pytest.log` before editing:
8 failed, 9937 passed, 221 skipped. The log has the built paired runtime,
headers, generated version header, Eigen, and 312 public corpus strategies.
Its skips concern unavailable private fixtures, historical references,
population inputs, clang, or empty parameter sets; no corpus compile skip
is listed.

## Cause and change per failing row

| Failing row | Cause | Change |
| --- | --- | --- |
| `test_scratch_facts.py::test_hand_computed_golden[doc_worked_vector]` | The flat certificate omitted a `00` byte in F's empty-string length and another in H's zero count: 80 bytes instead of 82. The independent layout was correct. | Hand-corrected only the flat certificate in `tests/data/scratch_receipt_golden.json:17`. |
| `test_scratch_facts.py::test_hand_computed_golden[sorted_multi_entry_and_nul_byte]` | The flat certificate omitted a `00` byte in each of F's and H's zero counts: 91 bytes instead of 93. The layout was correct. | Hand-corrected only the flat certificate in `tests/data/scratch_receipt_golden.json:59`. |
| `test_diagnostic_notes.py::test_real_interfaces_keep_notes_and_short_sentences[chart_ema]` | Its frozen successful wire contains C++ with the pre-V order-shapes receipt. | Inserted exactly the new `scratch_facts` field in that receipt inside `PRODUCT_BEFORE["wire"]["chart_ema"]`. |
| Same test, `[visual_only]` | Same missing receipt field. | Same insertion in this case's frozen wire. |
| Same test, `[display_request]` | Same missing receipt field. | Same insertion in this case's frozen wire. |
| Same test, `[actionable_warning]` | Same missing receipt field; its risk warning and `unmodeled` entry are unrelated. | Same insertion; preserved the warning and `unmodeled` entry. |
| `test_codegen_ta_dynamic_length.py::test_constant_and_input_lengths_keep_their_cpp` | The pinned historical scaffold predates `scratch_facts`; this probe has one literal entry ID `"L"`. | Added the hand-written `"L"` receipt fragment to the historical expected C++ only, then retained the whole-string equality assertion. |
| `test_e2e_session_history.py::test_scripts_without_a_read_keep_their_cpp[a script name spelled like a generated history member]` | Same historical receipt difference and same literal `"L"` entry. | Applied the same fixed expected fragment; retained whole-string equality and the compile check. |

`incomplete_exit_out_of_subset` was already consistent at 98 bytes and is
unchanged. No classifier, encoder, golden assertion, diagnostic assertion,
skip, or production file changed in this round.

## Independent byte arithmetic

Re-read the paired engine's `../wt/docs/design/scratch-facts.md:50-76`.
Wire v1 is one version byte, then S,F,E,H,J, each with one completeness
byte, eight count bytes, and eight length bytes plus data per entry.
All offsets below are half-open byte intervals.

| Vector | Version | S | F | E | H | J | Total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `doc_worked_vector` | `[0,1)` | `[1,20)` = 19 | `[20,37)` = 17 | `[37,55)` = 18 | `[55,64)` = 9 | `[64,82)` = 18 | 82 |
| `incomplete_exit_out_of_subset` | `[0,1)` | `[1,19)` = 18 | `[19,36)` = 17 | `[36,54)` = 18 | `[54,80)` = 26 | `[80,98)` = 18 | 98 |
| `sorted_multi_entry_and_nul_byte` | `[0,1)` | `[1,28)` = 27 | `[28,37)` = 9 | `[37,57)` = 20 | `[57,66)` = 9 | `[66,93)` = 27 | 93 |

Entry-by-entry derivation (each row also has its complete byte and eight
count bytes; every entry has eight length bytes before the listed data):

| Vector / set | Complete | Count | Entry byte lengths and raw bytes |
| --- | --- | --- | --- |
| worked S | 1 | 1 | 2: `61 62` |
| worked F | 1 | 1 | 0: empty |
| worked E | 1 | 1 | 1: `78` |
| worked H | 1 | 0 | no entries |
| worked J | 1 | 1 | 1: `71` |
| incomplete S | 1 | 1 | 1: `4c` |
| incomplete F | 1 | 1 | 0: empty |
| incomplete E | 0 | 1 | 1: `58` |
| incomplete H | 1 | 2 | 0: empty; 1: `4c` |
| incomplete J | 1 | 1 | 1: `4c` |
| sorted/NUL S | 1 | 2 | 1: `42`; 1: `61` |
| sorted/NUL F | 1 | 0 | no entries |
| sorted/NUL E | 1 | 1 | 3: `61 00 62` |
| sorted/NUL H | 1 | 0 | no entries |
| sorted/NUL J | 1 | 2 | 1: `42`; 1: `61` |

Thus the corrected worked certificate restores `00` at byte offsets 36
(F's eighth length byte) and 63 (H's eighth count byte). The sorted/NUL
certificate restores `00` at offsets 36 and 65 (F's and H's eighth count
bytes). These are zero-based offsets in the corrected certificates.

The layout blocks remain an independent, literal transcription. The flat
hex strings were corrected by hand; neither transcription is computed from
the other at test time. NUL remains the ordinary `00` byte inside `a\0b`.

## Baseline scope and exact formatting

Inspected the full source diff from C++ identity reference
`1e6eb83354b74ede1326fe55e576e91ea81f156b` to lane HEAD `c75a4fe8`,
including every changed generation path. The intervening array, import,
time-field, and syminfo changes do not apply to these probes; TA/session
changes on their paths are comments. The diagnostic test already handles
the declared note migration. No unrelated C++ difference was identified by
this static inspection.

The four diagnostic probes have no order calls. Their expected rosters are:

- S = H = `{"__close__", "__intraday_cap_close__", "__intraday_loss__",
  "__margin_call__", "__pine_close_all"}`, in that unsigned-byte order.
- F = E = J = `{}`; every roster is complete.
- The string lengths are 9, 22, 17, 15, 16 (79 bytes). Each S/H set costs
  `9 + 5*8 + 79 = 128`; the certificate costs
  `1 + 128 + 9 + 9 + 128 + 9 = 284` bytes.

Hand-inserted the resulting fixed field into each existing wire string.
It adds exactly 671 C++ characters in each case, after
`\"process_orders_on_close\":false,` and before `\"settings\":`.
Outer receipt keys remain sorted; the new object's key order is
`certificate`, `classification`, `reasons`, `version`. The existing C++,
wire-JSON, and fixture-JSON escaping levels are preserved.

A read of the original and edited fixture confirms every C++ character
outside that insertion is identical, including its final
`      });\n    }\n#endif\n#endif\n}\n`. Other envelope fields, all diagnostic
rows, error wires, and formatted error text are identical. The four
`interfaces.*.cpp_sha256` metadata values were updated from the manually
edited expected C++ strings; they are not runtime hash-stream pins.

For the two identity probes, `tests/_scratch_facts_baseline.py:4-55`
contains the fixed expected fragment and its derivation:
S has eight IDs including `"L"` and the documented internal labels;
F = `{"", "L"}`, E = `{}`, H = S union `{""}`, J = `{"L"}`.
Its certificate costs
`1 + 182 + 26 + 9 + 190 + 18 = 426` bytes. This literal is independent of
the emitter and is inserted only into the historical expected string.
The helper requires exactly one old receipt boundary.

Both `CPP_IDENTITY_REFERENCE` pins remain
`1e6eb83354b74ede1326fe55e576e91ea81f156b`. No new reference was generated,
no actual output is copied into an expected slot, and no field is stripped
from actual output. A change to any other C++ byte still fails comparison.
No unrelated baseline was updated or hidden.

## Sink-format check (DESIGN 3.7)

1. **Carrier:** `scratch_facts` is an additive member of the JSON document
   returned by the existing `strategy_order_shapes_receipt` export
   (`pineforge_codegen/codegen/order_shapes.py:350-359`). It appears inside
   the generated C++ string embedded in the glue success envelope. It is
   not a report-sink header, record, trailer, or sink receipt field.
2. **Pinned versions:** no existing version integer or string changed.
   `strategy_order_shapes_api_version()` remains `1u`
   (`codegen/capabilities.py:312`), the order-shapes document remains
   version 1, and codegen `VERSION` remains `1.6.2-rc.1`. The additive
   object's version is 1 and its certificate starts with wire byte `01`.
   Capability/confirmed-bar versions, sink version 1, ABI values, and the
   engine version string are unchanged by this lane and this fix.
3. **Sink documents/wire affected:** **NO. The sink did not move.**
   Read DESIGN 3.7 and the paired engine's `report_sink.h`,
   `report_sink_writer.hpp`, and `docs/design/cr150-contracts.md`.
   No sink field, type, unit, order, descriptor, schema/version, record
   tag, header, trailer, writer, or reader is modified. The JSON whose
   bytes change is the order-shapes metadata receipt alone.

## Validation and handoff

Only file inspection/editing and read-only git operations were used.
`git diff --check` reports no whitespace error. No local generator,
pytest, compiler, cmake, test binary, install, or repository script ran,
as required by the edit-only lane brief. These are authored corrections,
not a claimed passing proof.

The existing fix draft was re-audited before this handoff. A comma-delimited
word diff of `product.before.json` shows only four complete field insertions
and the four `cpp_sha256` metadata replacements; it shows no other removed
or inserted wire token. Both identity tests still compare the entire actual
C++ against the unchanged historical scaffold plus the fixed literal field.

Supervisor re-proof must cover the eight rows, the full engine-enabled
`python -m pytest -ra`, and the public corpus compile sweep with no compile
skip, using the paired headers, Eigen, generated header, built runtime,
and corpus. `DONE-fix1` signals this editing handoff only.
