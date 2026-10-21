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
