# Generated compile matrix: public SPEC draft

Draft revision: `2026-10-30.3`

Status: source-only draft. Current-source generation, producer tests,
translation, compilation and TradingView measurements remain UNEXECUTED or
NOT_MEASURED; no execution evidence is supplied by this document.

Initializer and collection-handle positions retain separate source constructs
and coverage obligations. Source comparison uses one shared lexical normal
form (see "Rendering, names, and hashes"). Its admission semantics belong to
generator version 3 while `schemaVersion` stays 1 (see "Interface version and
consumers"). The revision label, enumeration seed, domains, bounds,
constraints, controls and immutable seed bytes are retained. The historical
paired runtime engine pin remains ABSENT.

This specification defines the data and evidence contract for a generated
Pine v6 compile matrix. The companion data file is
[`coverage_spec.json`](../tests/fixtures/generated_compile_matrix/coverage_spec.json).
The generator owns enumeration, rendering, selection, and manifest creation.
The consumer owns real translation and compile-only execution. This document
does not change the transpiler, its support lists, the compiler helper, CI, or
the engine.

The matrix exists to make a CI acceptance decision depend on emitted C++ that
actually compiles against the exact paired engine headers. A source that only
translates is not a compile success. A compile-only success establishes
buildability against that pair; it does not establish TradingView value
parity.

## Contract data

`coverage_spec.json` uses `schemaVersion: 1` and keeps the finite source
inventory separate from implementation behavior. Its `draftRevision` is
`2026-10-30.3`. The `bounds` object is part of the contract:

| Bound | Value |
| --- | ---: |
| candidate enumeration maximum | 100000 |
| ordinary selected cases maximum | 512 |
| required community seeds | 3 |
| maximum total emitted sources | 515 |

Each axis has a name and an ordered finite list of string levels. A constraint
has an explicit `matches` map, a `classification`, a `reason`, and a
`languageBasis`. A matches map is a conjunction: every named axis must have
one of the listed levels. An axis absent from the map is unrestricted. The
spec contains no executable predicates, expressions, or evaluation hooks.

Constraint classifications are:

- `valid`: the matched role is a language-valid inventory cell.
- `inapplicable`: the selected dimensions do not describe the requested
  position, such as a collection-element position on a scalar.
- `invalid`: the combination is rejected by the Pine v6 language contract,
  such as a composite collection handle used as a map key.
- `unresolved`: the named evidence does not establish legality. The cell
  remains visible and cannot prove coverage.

The generator must preserve every axis level in its inventory. Constraints may
classify a combination; they may not silently remove a requested level.

`schemaVersion` remains 1, but `.3` changes policy semantics. A generator that
does not implement this revision must report incompatible input. It must not
ignore new fields and use `.2` enumeration or classification rules.

## Axes and language roles

The exact axis order and level order are in the JSON file. Their meanings are:

| Axis | Levels |
| --- | --- |
| `statement_kind` | `if`, `if_else`, `ternary`, `switch`, `for`, `while`, `function_definition`, `method`, `var`, `varip`, `tuple_return` |
| `body_layout` | `single_line`, `indented` |
| `primitive_type` | `int`, `float`, `bool`, `string`, `color` |
| `value_shape` | `scalar`, `array`, `map`, `matrix`, `udt_field_scalar`, `udt_field_array`, `udt_field_map`, `udt_field_matrix` |
| `missing_value_position` | `none`, `branch_order`, `return`, `initializer`, `argument`, `collection_handle`, `collection_element`, `udt_field` |
| `missing_predicate_target` | `none`, `container_handle`, `container_element`, `udt_field_container` |
| `map_key_type` | `none`, `int`, `float`, `bool`, `string`, `color`, `array`, `map`, `matrix`, `udt` |

`primitive_type` is the leaf or payload role. For a collection handle, it
records the element type even when the missing value is the handle itself.
`value_shape` explicitly includes a UDT field for every requested scalar,
array, map, and matrix family. `map_key_type` keeps primitive, composite, and
UDT roles visible; the UDT key cell is unresolved until an independent
language reference establishes its legality.

`missing_value_position` covers typed missing values in branch order, return,
initializer, argument, collection handle, collection element, and UDT field
positions. `missing_predicate_target` separately covers a missing-value
predicate applied to a container handle, a container element, or a container
held in a UDT field. These dimensions must remain separate in case metadata so
the array-field predicate witness cannot be confused with an array element
assignment.

`initializer` and `collection_handle` are distinct levels, never aliases.
For an eligible collection, `initializer` declares a typed value with `= na`
and carries it into the selected value, including through a UDT field.
`collection_handle` first constructs a present collection and then makes the
existing handle missing with `:= na`, before that handle is read as the
selected value. A UDT collection uses a present field followed by
`holder.value := na`; a plain collection uses a typed handle followed by its
reassignment. This applies to arrays, maps and matrices with every eligible
payload and map-key role. Renaming a variable or adding a metadata comment
cannot supply the distinction.

The JSON records the currently established relationships. A ternary and a
declaration use statement-level layouts; neither loses its rows because it
has no body of its own. Collection positions require a
collection shape, and a UDT-field position requires a UDT-field shape. A map
key is meaningful only for a map or UDT map field. Primitive map keys are
inventory-valid; array, map, and matrix handles are invalid key roles. A UDT
map key remains unresolved. A missing boolean leaf is invalid in Pine v6,
while a missing collection handle whose payload type is boolean remains a
distinct valid role. The complete varip aggregate restriction is unresolved
from the named evidence and is recorded as such.

For the same semantic fact, classification follows existing TV-observed
evidence with receipts, then Pine documentation, then implementation
observations. Current behavior cannot establish language invalidity or change
a desired positive outcome into a refusal. An unclassified input defaults to
`valid` and MUST translate AND compile. Keep explicit evidence conflicts
unresolved. Validity provenance is separate from this matrix's TV result,
which remains `NOT_MEASURED`.

Each constraint names a semantic `fact`. Resolve competing evidence for that
same fact at the highest available tier; a conflict within that tier is
unresolved. Across independent facts, apply `inapplicable`, then `invalid`,
then `unresolved`, then `valid`. For example, a primitive map-key role does
not make a boolean missing element valid. Constraints without an explicit
evidence tier refer to their named Pine documentation basis; an explicit
null tier records missing evidence. No implementation observation can override
a language-valid requirement.

The layout inventory applies to every requested form:

| Form | `single_line` role | `indented` role | Language basis |
| --- | --- | --- | --- |
| `if`, `if_else` | One physical body line per branch, under the required indented headers; comma-separated local statements can share it | Branch statements on separate indented lines | Conditional local-block grammar; recorded repository TV evidence |
| `switch` | Case/default body on its arm's `=>` line | Body on indented lines after the arm's `=>` | Switch grammar; recorded repository TV evidence |
| `for`, `while` | Inapplicable: no same-header-line loop body | Required indented loop block | [Loops](https://www.tradingview.com/pine-script-docs/language/loops/) |
| `function_definition`, `method` | Body follows `=>` on the definition line | Body in the indented block following `=>` | [Functions](https://www.tradingview.com/pine-script-docs/language/user-defined-functions/), [methods](https://www.tradingview.com/pine-script-docs/language/methods/) |
| `tuple_return` | Actual tuple expression follows the helper's `=>` | Actual tuple expression in its helper's indented block | Function tuple-return grammar |
| `ternary` | Complete ternary-bearing statement on one global line | Ternary-bearing statement in a valid enclosing indented block | [Operators](https://www.tradingview.com/pine-script-docs/language/operators/) and local scopes |
| `var`, `varip` | Complete declaration on one global line | Selected declaration inside a valid enclosing indented block | [Variable declarations](https://www.tradingview.com/pine-script-docs/language/variable-declarations/) |

`single_line` never means putting an `if` body after its condition on the
header line. For branch layouts it names the physical local-body line; for
declarations and ternaries it names the selected statement's placement. The
renderer must preserve these meanings rather than synthesize a nonexistent
declaration body. No form is classified wholly inapplicable.

The existing [TV receipt](../tests/fixtures/tail_f_tv/README.md) and
[source](../tests/fixtures/tail_f_tv/arm_commas.pine) establish the recorded
one-line `if`/`else` local-block and `switch` arm shapes.
[Declarations on arm lines](../tests/fixtures/tail_f_tv/arm_commas_decl.pine)
have a separate receipt there. These are existing language-evidence
provenances, not new measurements of generated cases. The documentation URLs
name language bases, not new measurements of this matrix.

For `branch_order`, render both missing-first and missing-last arms within the
case, keeping the selected payload type and shape. `return`, `initializer`
and `argument` refer to the selected value: a scalar leaf or a collection
handle, including one read from a UDT field. `collection_element` instead
targets the leaf stored in that collection. `udt_field` requires a selected
field, whose missing value is scalar or a handle according to `value_shape`.
The predicate target is independent. A bool handle can be missing; a bool
leaf cannot be missing, and `na(bool_element)` has no Pine v6 overload.
An omitted call argument in a deliberate invalid control is not a typed
missing-value position and is recorded separately as `invalidInputFact`.

## Candidate enumeration and pairwise selection

Revision .3 replaces the full seven-axis Cartesian metadata prerequisite with
two finite sets. Set A has one valid canonical completion for every applicable
`statement_kind × body_layout × primitive_type` core triple. Set B is a
deterministic two-way covering set over all seven axes. The fixed recorded seed
is the UTF-8 string `2026-10-30.3`. Every requested statement form must have
ordinary positive rows; missing completions are reported as incomplete.

The `zero_positive_form_rows` diagnostic names requested forms with no
eligible ordinary positive candidate under the declared constraints and
resolved classifications. It does not name otherwise eligible forms merely
withheld by another input issue. `formCounts`, `formPositiveCounts`,
`layoutCounts` and achieved facts still count only emitted ordinary positives;
candidate eligibility earns no emitted or compiled coverage. Withholding,
selection, cap, renderer and storage failures retain their existing issues,
uncovered requirements and incomplete outcomes. This diagnostic basis changes
neither generation nor admission policy.

For each core triple and each pair of levels from distinct axes, seek the first
valid complete assignment in declared axis/level order, pruning incompatible
partial assignments using the declarative constraints. The generator need not
construct the raw 281600-row product. A pair is a positive target when a valid
complete extension exists, independently of current compiler behavior. Retain
the reasons for inapplicable, invalid-only and unresolved pair inventory.

The candidate pool is the deduplicated union of these core completions, one
completion per positive pair target, and one per positive input fact.
`candidateCount` counts the actual complete assignments in that pool.
`enumeratedCaseCount` adds the explicit controls and three seeds and must
remain at most 100000. Partial search nodes and the unmaterialized raw
product are not those counts. A structural upper bound is computed from the
core-size product, sum of two-axis size products, axis-level fact slots,
statement-form and valid-constraint fact slots, and explicit control/seed
counts. It is distinct from an actual enumeration receipt. Set B covers the
positive target pairs and positive input facts from the pool, independently of
A: prefer greatest uncovered-pair gain, then uncovered-positive-fact gain,
then lowest SHA-256 of `seed + LF + canonical named-axis JSON`, then declared
axis/level order. No host PRNG, time, object identity or host ordering enters
the result. A zero-gain selection with uncovered targets is incomplete.

Pair satisfiability is determined from the declarative domain, rather than
only from the pool or selected rows. A valid extension makes a pair a positive
target. Without a valid extension, an unresolved extension makes it
unresolved; otherwise an applicable invalid extension makes it invalid-only;
the remaining pairs are inapplicable. Each inventory record retains its
witness axes or constraint/evidence reasons. This prevents selection from
defining its own apparently complete target set.

Compute `coreTupleCount`, `coreCaseCount`, `pairwiseCaseCount`,
`overlapCaseCount` and `positiveUnionCount` from the actual sets. Deduplicate
A and B by complete axis tuple and ordinary render family, retaining both
membership flags and selection orders. The emitted positive union count is
`coreCaseCount + pairwiseCaseCount - overlapCaseCount`. Pairwise coverage proves
two-way coverage; the full core cross proves only the named three-axis
interaction. Neither proves the full seven-axis cross, other higher-order
interactions, runtime behavior or TV equivalence.

The three explicit invalid controls consume ordinary slots:
`ordinarySelectedCount = positiveUnionCount + invalidControlCount <= 512`.
Exactly three immutable community witnesses are added, for a total at most 515.
Any cap conflict reports actual counts and uncovered targets; it cannot enlarge
the cap, shrink an inventory, filter current failures or truncate sources.
Neither controls nor seeds can hide an uncovered ordinary positive target.

Set A owns the full core tuple requirement. Set B's positive input facts are
`axis_level` for every level with a valid extension, `statement_form` for every
requested form, and `valid_constraint` for every valid-role constraint with a
valid extension. Facts carry their axis/level, form or constraint id.
All positive cases also carry source/render/provenance obligations.
Invalid controls prove only their explicit
refusal facts. Inapplicable and invalid-only inventory remain acknowledged
with language reasons; they are not positive targets. Unresolved selectors
and missing diagnostic evidence stay separately visible and cannot prove
coverage or a refusal. Resolved positive-domain completeness and unresolved
overall inventory must be reported separately.

## Rendering, names, and hashes

The ordinary render family must produce a real Pine v6 strategy for each
selected metadata row. The source must exercise the selected statement,
layout, primitive or payload type, shape, missing-value position, predicate
target, and map-key role wherever the row is applicable. It must not return a
constant in place of the selected construct, ignore an input, use a stub, or
special-case a current failure. Invalid-only, inapplicable and unresolved roles
remain represented by their finite inventory selectors, pair records and
constraint facts. They do not require a full Cartesian metadata table.

Changing an axis level within a common eligible context must change the
selected source construct. The authored properties derive eligibility from
the input domains and fact evidence, hold every other axis fixed, and compare
all eligible sibling levels. They compare the generator's own normal form, so comment-only and
consistent-rename-only differences earn no distinction, and they include
positive and ignored-input/comment-only/rename-only negative controls for each
axis, plus literal, operator and construct controls that must stay distinct.
The independent oracle's eligible set must equal the generator's own positive
classified set over the real SPEC product (one cached pass). Each axis group is
cross-checked against it, a dropped group or row or an added unsupported row
fails that check, every row the planner selects must be oracle-eligible and
must render, and no renderer hole is hidden by loosening the oracle. A structural property checks declaration versus subsequent handle
assignment and the selected-value path for every eligible collection context;
the `if / indented / int / array / none / none` example is also an
explicit regression. Coupled levels with no common eligible context remain
separate inventory roles; the properties do not declare them aliases.

The generator also compares ordinary sources by one lexical normal form
(`comparison_normal_form`, basis `lexical_normal_form_v1`) before emission, and
the authored properties call the same function. The form drops comments outside
quotes, blank and comment-only lines and whitespace between tokens. It keeps
each line's indentation, string and number literals, operators, keywords,
built-in and member names, and it renames every declared user name (type,
field, function, parameter, variable, loop counter) by order of first
occurrence. Tokens are typed, so a string, a number and a renamed identifier
never collide, and `x + x` differs from `x + y`. A comment marker inside quotes
stays string data. If different selected rows share one normal form, the
generator records `renderer_source_collision` with both case ids, both
exact-byte SHA-256 values and the shared comparison hash, leaves the duplicate
un-emitted, and stops later emission. The exact-bytes `sourceSha256` stays the
content identity; `comparisonSha256` is a separate fingerprint. A form it cannot
read (an unterminated string, tab indentation, a `for ... in` binding, an
unknown character, a declared name equal to one of the reserved built-in names
the generator specifically lists) records
`renderer_source_normal_form_unsupported` with a named reason and the row
stays INCOMPLETE. A row is never dropped or reclassified to force a pass.
The collision rule (selected rows that share one normal form are INCOMPLETE
even when their exact bytes differ) and the unsupported-form issue are
generator version 3. Version 2 keeps its historical meaning and does not
define them; `schemaVersion` stays 1 (see "Interface version and consumers").
Coverage is recomputed from sources actually emitted. Planned targets remain
due, existing files are retained, and generation is incomplete. An
input-ignoring renderer cannot earn two emitted coverage credits from one
normal form. Limits: spelling-level bijective renames only, scope is not
tracked, and this is no Pine compiler and no semantic-equivalence or
compiler-correctness claim. The normal form is a spelling-equivalence guard,
not a relevance or semantic-equivalence check. It keeps every literal and
statement, so a dummy declaration or a literal that varies from row to row can
give a source a distinct form, and an input-ignoring renderer that adds either
is not caught by this guard. A distinct form alone earns no claim that a source
exercises its axis level. Relevance must come from axis-relevant construct
assertions and, later, from compiler and runtime evidence; this guard supplies
neither. The reserved-name refusal covers only the built-in names the
generator lists, and no claim is made that the list holds every Pine built-in
name. Stating these limits adds no check.

`render_case` retains its public middle return slot for scope issues, but the
current renderer has no defined producer for that list. Empty-list assertions
are not source-fidelity checks. Tests now assert the selected source path
instead. The existing `renderer_scope_unresolved` consumer branch remains
reserved; defining future issue-producing conditions requires an explicit
contract revision. This draft does not invent issues to exercise it.

The community render family uses the exact source fragments in the
`seedFixtures` array:

```pine
//@version=6
strategy('repro')
type Box
    array<string> items
```

That header is followed immediately by exactly one body and then the exact
tail:

```pine
if bar_index == 5
    strategy.entry('L', strategy.long)
```

The three bodies are:

```pine
var b = Box.new(array.new<string>())
add(Box x, string s) => array.push(x.items, s), x.items.size()
add(b, 'a')
```

```pine
var b = Box.new(array.new<string>())
take(Box x) => x.items.size() > 2 ? x.items.shift() : na
b.items.push('a')
take(b)
```

```pine
var b = Box.new()
if na(b.items)
    b.items := array.new<string>()
```

The assembled seed is `header + body + tail`, byte-for-byte. No cast,
multiline rewrite, constructor workaround, normalization, or other repair is
permitted. Each seed has its source SHA-256 in the generated manifest.

An ordinary stable id is `case-` plus the full lower-case SHA-256 of canonical
UTF-8 JSON with keys `axes` (the complete named-axis object) and `renderFamily`
(`ordinary`). Sort all object keys, use compact comma/colon separators, and
append no newline. The pairwise rank hashes the same compact named-axis
object alone, prefixed by the seed and one LF. Core and pairwise membership do
not change a row's id.

A control uses `control-<invalidControls.id>`; a seed uses its unchanged
required seed id. Source hashes are SHA-256 of exact disk bytes, independently
of stable-id hashes. Sort case records by render family then id, and preserve
the separate core and pairwise selection orders. Repeated generation must
yield identical filenames, ids, source bytes, hashes and metadata across hosts.
The manifest also hashes the raw spec, generator source and seed index.

The expected seed receipts are preserved from the
[`seed-index.json`](../tests/fixtures/generated_compile_matrix/seed-index.json)
for [public issue #281](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/281);
the generator must independently reconstruct and hash them:

| Seed | Bytes | SHA-256 |
| --- | ---: | --- |
| `single_line_parameter_after_comma` | 233 | `0c42aca26fa0199fbd4547efb48fe08e4f2e789954aba075584731a2318c842d` |
| `ternary_string_missing_branch` | 241 | `9d9aac30e97db1448e8aeb392ecfb4e3ad3ce3be0ab535353acc7d4da74566c8` |
| `array_string_field_missing_predicate` | 189 | `9cd4381f340e216579c6616108e77cf523ba625f2281b16fcde254a777911cac` |

Their supplied input SHA-256 is
`ea777c2c79898a26ab1767bd95ce16fcf5f321513bbdec69149087a5c775a350`.

## Expected outcomes

Every generated case carries a classification and expected translation,
compile, and TradingView status. The generic outcome table is in
`expectedOutcomes`:

| Classification | Translation | Compile | TV |
| --- | --- | --- | --- |
| valid intended-supported | `MUST_SUCCEED` | `MUST_SUCCEED` | `NOT_MEASURED` |
| valid but currently unsupported | `MUST_SUCCEED` | `MUST_SUCCEED` | `NOT_MEASURED` |
| explicit language-invalid control | `MUST_REJECT_WITH_EXPECTED_PF_E` | `NOT_APPLICABLE` | `NOT_MEASURED` |
| unresolved legality | `UNKNOWN` | `UNKNOWN` | `NOT_MEASURED` |

Legal intended-supported cases are positive compile requirements independent of
present defects. `VISIBLE_GAP` is an implementation observation, never a
relaxed expected result: a refusal or build failure on a valid row remains
failure. A language-invalid control is not positive compile coverage.
An unresolved cell makes the relevant evidence incomplete and cannot be
promoted to a positive result by a translator response.

The deliberately small refusal set consists of the three exact sources in
`invalidControls`, each derived from `_typed_method_source` and the
malformed-call parameter table in
[`test_map_call_diagnostics.py`](../tests/test_map_call_diagnostics.py).
The intended codes are read from
[`diagnostics_catalog.json`](../pineforge_codegen/diagnostics_catalog.json)
and matched to the existing emitter's message/hint templates:

| Control | Deliberate fault | Required diagnostic | Expected phase |
| --- | --- | --- | --- |
| `map_get_unknown_keyword` | `target.get(foo="x")` | `PF-E5016` | codegen |
| `map_get_missing_key` | `target.get()` | `PF-E5020` | codegen |
| `map_get_duplicate_key_binding` | `target.get("x", key="y")` | `PF-E5019` | codegen |

These invalid spellings follow the documented `map.get` signature and
single-binding argument rule. Other failures in those tests caused only by
current implementation limits are not selected as invalid controls. The
consumer must obtain the intended PF-E code and phase through the public
translator and retain every diagnostic and the work's actual exit. A different
error, crash or accepted input cannot prove an expected refusal.

Composite map-key and boolean missing-leaf roles retain their language-invalid
inventory classifications. Their intended PF-E mappings are not established
here, so `unresolvedEvidence` records that omission rather than emitting them
as successful-refusal requirements. The existing UDT-key and varip aggregate
selectors remain explicit unresolved language evidence. None of these
inventory records creates a full invalid cross or hides a current valid
failure.

The three seed witnesses are all intended-supported positive outcomes:

| Seed | Issue-reported baseline phase | Desired outcome after the root fix | TV status |
| --- | --- | --- | --- |
| `single_line_parameter_after_comma` | front-end rejection: unknown parameter after comma | translation succeeds, then C++ compiles | issue provenance; `NOT_MEASURED` |
| `ternary_string_missing_branch` | translation succeeds, then C++ compile fails on string versus numeric return type | translation succeeds and C++ compiles | issue provenance; `NOT_MEASURED` |
| `array_string_field_missing_predicate` | translation succeeds, then C++ compile fails on the container predicate overload | translation succeeds and C++ compiles | issue provenance; `NOT_MEASURED` |

The phase labels above preserve the supplied issue's distinct failure classes.
They are not a measured baseline RED verdict. A remote baseline run
must record the actual phase, exit code, diagnostics, and source digest before
any RED result is reported.

## Generator command and manifest

The generator CLI contract is:

```text
python3 scripts/generate_compile_matrix.py \
  --spec tests/fixtures/generated_compile_matrix/coverage_spec.json \
  --output-dir <new-output-directory> [--seed-index <index.json>]
```

`--spec` and `--output-dir` are required; the output parent must exist.
`--seed-index` defaults to
`tests/fixtures/generated_compile_matrix/seed-index.json`.
The generator refuses any reused directory, including an empty one, before
writing. No installer or package entry point is added. It writes deterministic
`.pine` files below `cases/` and `manifest.json` containing at least:

- schema and draft revision, generator version/source hash, spec source hash,
  input identities and the complete specification;
- the actual candidate, core, pairwise, overlap, deduplicated positive union,
  invalid-control, ordinary, seed and total counts;
- each case's stable id, source path and source hash;
- every axis level for the case, applicable constraints, legality, expected
  translation/compile/TV status, separate validity provenance, core/pairwise
  membership flags, expected diagnostic and seed provenance;
- target/achieved/uncovered core tuples, all pair inventory, target/achieved/
  uncovered pair and positive-fact sets, per-form positive counts, and
  separately categorized invalid/inapplicable/unresolved inventory;
- generation completion separately from consumer test results;
- per-case translation, compile, and TV result records;
- codegen, engine, header, Eigen, generated-header, and compiler identities
  and exit/diagnostic data when a consumer has run.

The producer properties live in
[`test_generated_compile_matrix.py`](../tests/test_generated_compile_matrix.py).
They load the generator from `scripts/generate_compile_matrix.py` and the
declared fixtures from `tests/fixtures/generated_compile_matrix/`, relative to
the repository root. Test outputs use pytest's temporary directory rather
than a directory in the source tree. These properties exercise generation
only; they do not invoke translation, compilation or TradingView.

Inputs and output paths must have no symlink component. The generator and
test readers refuse sensitive names before file access: `.ssh`, `.aws`,
`.codex`, `.config/zsh/keys`, `.config/pineforge`, and any component ending
in `.env`, with case-insensitive name checks. Each followed input is named
before it is read. A future environment must use real, permitted paths; a
path refusal is an incomplete input, never source or coverage success.

Generation completion is not test success. A `.exit` or `DONE` record, if an
orchestrator uses one, records the generator's own exit code, with `pipefail`
for any pipeline. It cannot be used as the verdict. A run script must include
one deliberate crash row to prove that a crash writes a non-zero own-exit
record. The verdict comes from the manifest/result files and the judge's
output.

The CLI propagates the generator's own exit: 0 for completed generation, 2
for incomplete input/coverage, and 1 for a generation/storage crash. An
incomplete manifest is retained when storage permits. There is no generated
completion marker. Every result row starts with translation and compile
`UNEXECUTED`, TV `NOT_MEASURED`, and null exits, diagnostics and identities;
`results.status` starts `INCOMPLETE` even after completed generation. Counts
that have not been computed are null, not fixed zero.

The generator and producer tests have no wall-clock bound, alarm or
search-node limit. Finite domains and the existing retained-candidate/case
bounds do not establish elapsed runtime. A future authorized off-box runner
may be stopped by its operator. Stopped work is INCOMPLETE with a non-zero own
exit, and an absent manifest cannot establish completion. There is no
automatic elapsed bound, per-test bound, alarm or
elapsed-time acceptance, and no new count-based acceptance gate.

Within `coverage`, `core` holds axes, target/achieved/uncovered tuples,
case ids and selection order; `pairwise` holds axes, the fixed seed, case ids
and selection order. `overlapCaseIds` and `positiveUnionCaseIds` retain the
actual intersection and union. `achievedPairs` and `achievedInputFacts` are
recomputed from B; their uncovered sets are targets minus achieved. Core
achievement is the projection of emitted ordinary positive rows. Neither
controls nor seeds earns that credit. `basis=generation_metadata_only` and
`positiveCompileCoverage=UNEXECUTED` keep metadata evidence separate from real
compilation.

The manifest must use `INCOMPLETE` when headers, Eigen, the generated version
header, the paired engine, or the compiler are unavailable; when zero cases
are collected; when translation succeeds but compilation was not performed;
when a required compile was skipped; when an outcome is unknown; or when a
seed is missing or its source digest differs. It must retain the raw
translation/compiler phase, exit, and diagnostics rather than replacing them
with a marker-derived success.

## Interface version and consumers

The manifest header carries `schemaVersion`, the integer `1`, and
`generatorVersion`, the string `"3"`. `schemaVersion` is unchanged. The
generator's admission semantics changed (see "Rendering, names, and hashes"):
selected rows that share one normal form are INCOMPLETE even when their exact
bytes differ, and a form the generator cannot read records
`renderer_source_normal_form_unsupported`. Those semantics are generator
version 3. A change of admission semantics is not compatible by omission, so a
consumer must not read a version 3 manifest under version 2 rules. Version 2
keeps its historical meaning and is not redefined here.

Version 3 keeps the manifest field `sourceComparison` and, on each ordinary
case, `comparisonSha256` and `comparisonBasis`. `sourceComparison.basis` is
`lexical_normal_form_v1`; its `limits` text states that the form is
spelling-level only, tracks no scope and makes no semantic-equivalence claim.
The exact-bytes `sourceSha256` stays the content identity of every source and
is what a consumer verifies. `comparisonSha256` is a fingerprint under the
named `comparisonBasis`, means nothing under another basis, and never replaces
`sourceSha256`.

Downstream integration and consumer version pins are UNKNOWN here. This
producer supplies no CI or lab consumer wiring or consumer acceptance evidence.
Each consumer must pin
`generatorVersion` `"3"` as a string, `schemaVersion` `1` as an integer and the
exact source pins it was reviewed against (the `generatorSourceHash`,
`specSourceHash` and `inputIdentities` the manifest records), and it must
refuse any other or missing value as incompatible input, reported as
INCOMPLETE. It must not default to version 2 or accept an unknown
`generatorVersion` silently. The version string does not identify source bytes;
the source pins do. Any other consumer of this manifest, such as another lab
recipe or manifest reader, is UNKNOWN, not asserted absent, and must be
inventoried before the interface is integrated or released. Source inspection
does not establish consumer approval or successful integration.

## Compile-only consumer handoff

The consumer receives a completed manifest and reads the source files named by
it. For each positive case it calls the real public
`pineforge_codegen.transpile` API
and then the existing `tests/_compile.py` helper's `compile_cpp` against the
paired engine include tree, Eigen include tree, and generated version-header
tree. The compile call uses the helper's C++17 path and the engine's
`STRATEGY_FP_FLAGS` for a strategy translation unit. The consumer records the
actual codegen revision, engine revision, header and Eigen identities,
generated-header identity, compiler command/version, translation result, and
compiler result.

The handoff does not add a second compiler runner, stub a translator, suppress
an error, or treat a translator-only result as compilation. The existing
helper's opt-in environment contract remains authoritative. A missing
required input is `INCOMPLETE`, not a skip that can become green.

The consumer must establish exact engine, Eigen and generated version-header
inputs before loading the helper, whose standalone fallbacks can otherwise
skip or synthesize a version header. The helper returns `None` on success and
raises an assertion with a diagnostic preview on compiler failure; its return
alone does not expose every raw compiler receipt. Required phase/exit/raw
diagnostic evidence remains a consumer integration obligation. Leave
unavailable native receipts null and the outcome incomplete rather than
fabricating them. No helper changes or additional compiler runner are
authorized by this specification.

For a future complete remote acceptance, generation must finish within the
candidate and case caps with no uncovered legal pair or required fact; all
three exact seeds must be present; every positive intended-supported case must
translate and compile; explicit invalid controls must produce their intended
PF-E diagnostic; and every result must have real evidence and identities. A
valid unsupported gap or unresolved legality remains visible and prevents a
claim that the matrix is complete until the corresponding support or language
evidence is addressed.

The machine-readable `acceptanceRows` specify the later independent work,
all currently `UNEXECUTED`:

| Row | Required independent evidence |
| --- | --- |
| `core_and_forms` | Recomputed full applicable core and emitted projection; no uncovered tuple or requested form with zero ordinary positives |
| `all_axis_pairs` | Recomputed existential pair targets/reasons and B's achieved/uncovered sets; fixed-seed repeatability |
| `counts_caps_and_hashes` | Recomputed A/B/intersection/union counts, controls/seeds, every cap, stable ids and exact source/input hashes |
| `invalid_controls` | Real intended PF-E codes, phases, own exits and diagnostics for the three deliberate faults |
| `immutable_baseline_witnesses` | Actual pre-fix phase/exit/diagnostic RED receipts and later unchanged-source translation/compile receipts |
| `positive_compilation` | Every valid source translates and compiles with real public translation and the existing helper |
| `final_gates` | Full paired-engine pytest and corpus sweep without required skips, real CI and independent review |

The positive domain can finish generation while unresolved inventory remains
explicit; that does not establish complete inventory acceptance. These are
authored requirements, not review or verification results.

The UDT map-key and varip aggregate legality selectors remain unresolved;
composite-key and missing-boolean-leaf diagnostic mappings remain absent.
Their evidence cannot be supplied by empty renderer issues or an exit code.
With the declared unresolved evidence, even successful positive-domain
generation leaves overall `coverage.status=INCOMPLETE` and all consumer
results UNEXECUTED. The existing selection tests check repeatability and
seeded ranks, but do not directly prove that a changed seed changes a tied
selection. The small selection oracle does not cover per-fact evidence
precedence. The new source-eligibility oracle supports fact-tier resolution
for comparisons; it is not an independent selection-precedence test. Those
coverage gaps remain explicit and unmeasured.

## Pre-fix baseline and later TV extension

The pre-fix RED recipe requires separate authorization for a remote
environment. The supplied baseline codegen revision is
`b1ba934180665126ffd671709f39878e99838e1b`. The community short hash
`ec44968` is an unverified issue report; the runtime engine pin bound to this
packet is ABSENT. It cannot identify the paired runtime or justify a RED run.
The responsible owner must supply and retain the exact engine identity,
paired header trees, Eigen, generated header and compiler before that future
recipe can be used. No engine pin is inferred here.

That future recipe feeds the three exact seed sources to the public
translator. For a successful translation it invokes the real compile helper.
It records each source digest,
translation phase and exit, compiler phase and exit, raw diagnostics, and all
tool identities. The first witness is expected to fail in the front end; the
other two are expected to translate and fail during C++ compilation. The
remote result, rather than the issue text or a marker, establishes whether
that baseline RED is present.

The generator must not inspect those outputs and then alter, filter, or
rewrite a seed. After the root fixes, the same immutable seeds are rerun with
the paired fixed codegen and engine and are required to reach the desired
translation-plus-compile outcome.

TradingView checks are a later extension. New rows must carry explicit
`TV` status and preserve their source, codegen, engine, and feed provenance.
The current status for every case is `NOT_MEASURED`; the issue author's
statement that all three run on TV is provenance, not a new TV value
measurement. Compile-only evidence must never be described as runtime parity.

The independent checks named in the data file are authored requirements:
recompute enumeration and classifications independently, recompute manifest
hashes and coverage sets, and repeat generation to compare all bytes and
metadata. They are `UNEXECUTED_IN_THIS_SOURCE_ONLY_STAGE`.
