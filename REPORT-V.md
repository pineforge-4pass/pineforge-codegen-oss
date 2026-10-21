# core-cr150-o1v-cg — lane V step 1: codegen emission of the scratch vocabulary certificate

Status: AUTHORING ONLY. Nothing was transpiled, compiled, run or staged on this
machine. Worktree `cg`, branch `cx/o1-v-cg`, codegen main `dbf50b89`.
Deliverable is uncommitted.

The engine-side reader and its tests live in the paired engine worktree
(`core-cr150-o1v/wt`, see `REPORT.md` there). This file covers the codegen half.

## 1. Files changed

| File:line | Change |
| --- | --- |
| `pineforge_codegen/codegen/scratch_facts.py:1` | NEW module. Indexes one analyzed program, computes the five rosters S,F,E,H,J of the lane F contract, encodes them in wire v1 and returns the additive `scratch_facts` object. |
| `pineforge_codegen/codegen/order_shapes.py:350-359` | Adds the one field `"scratch_facts": classify_program(emitter.ctx.ast)` to the existing `strategy_order_shapes_receipt` JSON document. No new export, no version change. |
| `tests/test_scratch_facts.py:1` | NEW pytest: 34-program truth table, node-kind mutants, determinism, cross-language golden, roster shape. |
| `tests/test_scratch_facts_corpus.py:1` | NEW pytest: corpus census over every `corpus/validation/*/strategy.pine`. |
| `tests/data/scratch_receipt_golden.json:1` | NEW. Three hand-computed wire-v1 vectors, each with a per-byte `layout` and an independently retyped `certificate` hex. |
| `tests/test_order_shapes.py:25-26` | The exact receipt key-set assertion now includes `scratch_facts`. Nothing else in that test changed; the legacy receipt fixture still compares only `documents[:2]`. |
| `docs/PUBLIC_CONTRACT.md:96-107` | Documents the additive field and records that `strategy_order_shapes_api_version()` stays 1. |

## 2. The emitted object

```json
"scratch_facts": {
  "version": 1,
  "classification": "in_subset" | "out_of_subset",
  "certificate": "<lowercase hex of the wire v1 bytes>",
  "reasons": ["<short stable reason code>", ...]
}
```

Wire v1 (`docs/design/scratch-facts.md:50-62`, `scratch_facts.hpp:286-291`):
version byte `01`; then S, F, E, H, J in that order; each set is one complete
byte (`00`/`01`), a u64 little-endian count, then per entry a u64 little-endian
byte length and the raw UTF-8 bytes. Entries are sorted by **unsigned byte**
value and deduplicated. `encode_certificate` (`scratch_facts.py:515`) sorts a
built set of encoded byte strings, so its output cannot depend on Python dict or
set iteration order. Empty strings, NUL and `0x1f` are ordinary bytes.

LiveBounds is NOT emitted (neither field nor value): roots stay on the exact
history until lane R proves live bounds. A strategy with `pyramiding` left at
its default or large is still classified by its sets alone; no live bound is
invented.

## 3. api_version finding

`docs/PUBLIC_CONTRACT.md:67-95` and `CHANGELOG.md:544-575` fix
`strategy_order_shapes_api_version()` at 1 and enumerate the document's keys;
neither says an additive field requires a version bump. The one "no new fields
are added to the original receipt" sentence (`docs/PUBLIC_CONTRACT.md:63`)
concerns the capabilities receipt, not order shapes. Kept version 1; the field
is additive and ignorable. The only test that pinned the exact order-shapes key
set is `tests/test_order_shapes.py:25-26`, updated.

## 4. Closure rule as implemented

Rosters (full detail in the engine `REPORT.md` §3):

* S: `strategy.entry`/`strategy.order` ids **and** `strategy.exit`/`close`/
  `cancel` ids (each is a placement source ID in the adapter), plus the
  engine-created label families (`__margin_call__`, `__close__`,
  `__pine_close_all`, `__intraday_loss__`, `__intraday_cap_close__`,
  `__margin_preopen__` + entry id, `__close__` + entry/exit id);
* F: `strategy.exit` `from_entry` values (an omitted argument is `""`) plus,
  whenever any placement is possible, `""` and every known entry/exit ID (a
  close placement copies its own ID into `from_entry` on the non-default-FIFO
  path; a preopen margin slice copies the opening source ID), so F's
  completeness needs both rosters;
* E: `strategy.exit`/`strategy.close`/`strategy.cancel` ids;
* H: `S ∪ F` (the only `cohort_for` arguments in the adapter);
* J: the entry/order ids (the only IDs `note_exit_rank_key` records).

Handled node kinds and their verdicts are tabulated in the engine `REPORT.md`
§3. The short version: literals are IN; ternary, switch-of-values and
constant-string concatenation are IN when every branch is IN; a scope-matched
identifier is the union of its definitions; a single-expression user function
is the union of its call sites' arguments; `input.string` records its declared
default; **everything else is OUT**, including unknown node kinds.

## 5. Tests and why they fail before

| Test | Fails before because |
| --- | --- |
| `tests/test_scratch_facts.py::test_truth_table` (34 rows) | no `scratch_facts` key in the emitted document, and `pineforge_codegen.codegen.scratch_facts` does not exist (import error). |
| `tests/test_scratch_facts.py` mutant tests | same missing module. Each mutant swaps an IN program's id node for one OUT node kind (or an unknown `ASTNode` subclass) and requires OUT. |
| `test_same_program_emits_the_same_certificate_twice`, `test_declaration_order_does_not_change_the_certificate`, `test_encoder_is_independent_of_iteration_order_and_duplicates` | same. |
| `test_hand_computed_golden` | the golden file and the encoder are new. |
| `tests/test_scratch_facts_corpus.py::test_every_validation_probe_is_classified` | same missing field/module; skips when the sibling corpus is absent. |
| `tests/test_order_shapes.py` (updated key set) | before the change it asserts the old key set, which the new field would break; after the change it pins the new one. |

The truth table's expected classifications and reasons are hand-written,
one-line judgements. The corpus test asserts no count: it requires every probe
in the corpus tree (the static census population, Appendix E) to be `in_subset`
and prints any OUT probe with its reasons.

## 6. Deviations and unverified

* Conservative beyond the V brief: the extra internal source labels in S, `""`
  in F, cancel IDs in E, multi-statement user functions always OUT, numeric
  constants via `str.tostring` always OUT.
* `input.string` is IN with the declared default recorded, as the brief
  requires. The receipt cannot observe host overrides; the engine `REPORT.md`
  §7 raises this as the open safety question for lane H.
* UNVERIFIED: no transpile, pytest, compile or corpus run was performed. In
  particular the 34-row truth table and the corpus census are predictions from
  a static read; the Python emitter's existing tests were not re-run.

## Fix round 2b

Authoring only, in `core-cr150-o1v/cgwt`, branch `cx/o1-v-cg`, starting at
`0722852773`. The existing 114 added test lines were read and preserved.
This lane edits files only: nothing was staged or committed. The supervisor
owns the commits and all lab proof.

P0 cause and fix:

* `_body_value` walked backwards looking for any `.expr`, skipping declarations
  and assignments. A nested `SwitchStmt` also has `.expr`, but it is the
  selector, not the selected arm's value.
* `_function_return` accepted a one-statement body with any `.expr`, with the
  same selector mistake. `is_single_expr=True` additionally bypassed its body
  shape check.
* `_body_value` now checks the entire block for `ExprStmt` nodes before
  selecting its last expression. An empty body or any other statement makes
  the result incomplete with `unrecognized_expression`; no earlier or later
  expression can rescue that block. This includes every nested switch,
  regardless of its selector, arm values or default arm.
* `_function_return` now requires exactly one `ExprStmt`, for either syntax
  flag. Other shapes return incomplete with `user_function_nonconstant`.
  Neither helper obtains a value from an arbitrary statement's `.expr`.
  Existing flat switch expressions whose arms are expression statements,
  including `switch_of_literals`, retain their explicit arm-union handling.
  Flat single-expression functions retain argument binding.

Commit partition for the supervisor (no commits were made here):

1. `test(scratch_facts): truth-table rows for switch and non-ExprStmt value positions`
   — `tests/test_scratch_facts.py` only. Keep production and golden at the
   supplied HEAD for the RED lab proof.
2. `fix(scratch_facts): fail closed on any statement the value classifier does not model`
   — `pineforge_codegen/codegen/scratch_facts.py` and this report.
3. `fix(scratch_facts): refresh the codegen receipt golden to its documented layouts`
   — `tests/data/scratch_receipt_golden.json` only. See the P1 finding below:
   the supplied HEAD already repaired the certificate bytes, so this round's
   golden diff documents the independent byte audit.

Expected RED on commit 1, from source inspection only:

| New value-position rows | Current path that incorrectly yields IN |
| --- | --- |
| `tail_switch`, `nested_switch_ternary_branch`, `switch_default_arm` | `_function_return` reads the tail switch's `mode` selector and binds it to the finite input default, ignoring the arms. A ternary unions this false finite result with its literal branch. |
| `nested_switch_arm` | The inline function returns a genuine expression wrapper around the outer switch; `_body_value` then reads the inner switch's selector as the arm value. |
| `local_before_returned_expression`, `assignment_before_returned_expression` | `_body_value` returns the trailing `"OPEN"` without examining the preceding declaration or assignment. |
| `assignment_after_deciding_expression`, `local_after_deciding_expression` | `_body_value` skips the trailing non-expression statement and returns the earlier `"OPEN"`. |
| `nested_switch_before_returned_expression` | `_body_value` returns `"AFTER"`, ignoring the preceding switch even though its arms are all finite. |
| `nested_switch_after_deciding_expression` | `_body_value` reads the trailing switch's literal selector, ignoring its arms and missing default. |

All ten rows require OUT and independently check that the flat identity
function's emitted receipt remains IN with no reasons. The original truth
table and every pre-existing assertion are retained.

The twelve added `MUTANTS` entries are also expected RED on commit 1:
`nested_switch_arm`, `nested_switch_ternary_branch` and
`switch_default_statement` read a switch selector through `_body_value`;
`nested_switch_before_value` ignores the switch before the last literal;
`nested_switch_without_default_after_value` reads the trailing selector;
`local_before_value`, `assignment_before_value`, `assignment_after_value`
and `local_after_value` skip the non-expression statement;
`unknown_statement_before_value` and `unknown_statement_after_value` skip
an unknown statement; `unknown_statement_with_expr` mistakes an unknown
statement's finite `.expr` for its value. Each mutant now checks a flat
identity-function control as well as the literal control, and classifies
the actual mutated input.

`FUNCTION_BODY_MUTANTS` adds both syntax flags for each shape. The tail
switch with a default, tail switch without a default, and unknown statement
with `.expr` are expected RED under both flags: the old helper returns their
finite selector/attribute. `local_before_value` and `assignment_after_value`
are expected RED with `is_single_expr=True`, which bypasses the old body
check. Their block forms already return OUT and are retained as checks.
Every variant independently checks a flat function control. These are static
predictions, not observed test outcomes.

P1 finding and independent golden recomputation:

The stated stale codegen certificates are already fixed in the supplied
HEAD. `git show 0722852773 -- tests/data/scratch_receipt_golden.json` shows
both corrections in fix round 1. The committed first and third certificates
have 164 and 186 hex characters, respectively (82 and 93 wire bytes); the
160/182-character values were their predecessors. No layout, set,
classification or certificate byte has been changed in this round. The
golden-only diff adds the hand-counted boundaries and hex lengths to the
three notes.

Recomputed each vector by hand from its unchanged documented `layout`:
write version `01`, then S,F,E,H,J; each complete flag is one byte, each
count and length is eight little-endian bytes, then the raw payload.
An empty roster still has its full eight-byte zero count; an empty string
still has its full eight-byte zero length. No encoder, decoder, script or
transpiler produced these expected bytes.

| Vector | Payload/count audit in S,F,E,H,J order | Set sizes in bytes | Total including version |
| --- | --- | --- | --- |
| `doc_worked_vector` | All complete; counts 1,1,1,0,1; lengths 2,0,1,-,1; payloads `6162`, empty, `78`, none, `71`. | 19,17,18,9,18 | `1+19+17+18+9+18 = 82` bytes / 164 hex characters |
| `incomplete_exit_out_of_subset` | Only E incomplete; counts 1,1,1,2,1; lengths 1,0,1,(0,1),1; payloads `4c`, empty, `58`, (empty,`4c`), `4c`. | 18,17,18,26,18 | `1+18+17+18+26+18 = 98` bytes / 196 hex characters |
| `sorted_multi_entry_and_nul_byte` | All complete; counts 2,0,1,0,2; lengths (1,1),-,3,-,(1,1); payloads (`42`,`61`), none, `610062`, none, (`42`,`61`). `42` precedes `61`; NUL is a payload byte. | 27,9,20,9,27 | `1+27+9+20+9+27 = 93` bytes / 186 hex characters |

Zero-based S,F,E,H,J start offsets are 1,20,37,55,64 for the worked
vector; 1,19,36,54,80 for the incomplete vector; and 1,28,37,57,66 for
the sorted/NUL vector. Reading the committed certificate bytes at these
boundaries agrees with the unchanged layouts. No layout defect was found.
The referenced `docs/design/scratch-facts.md` is not in this codegen
checkout; this audit uses the wire format and layouts documented locally
in the golden and the existing report. The engine worktree was untouched.

Validation: **not run**. The round 2b platform rule forbids Python, pytest,
repo scripts, builds and proof runs on this Mac (`LB-446/test-runner`).
The only commands used were file/history reads; all authoring used patches.
UNVERIFIED: actual RED on commit 1, actual GREEN on commit 3, the complete
engine-enabled suite and skip review, the corpus compile sweep and corpus
differential, and agreement with the separate engine documentation/decoder.
These remain the supervisor's lab proof. No further undecided classifier
case was found in the scoped static review.

DONE-fix2b-codegen
