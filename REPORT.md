# A5-CGEN — compiler script-state bounds

**Status: INCOMPLETE.** This change supplies allocation evidence and removes the
generated UDT narrow-ID capacity stop. It does not install preallocation or
external-store adapters. In particular, an `external` metadata row does not
mean that its container has moved out of resident storage. The query reports
`current_storage = resident` explicitly
(`pineforge_codegen/codegen/state_bounds.py:166`,
`docs/SCRIPT_STATE_BOUNDS.md:33`). No flat-memory completion is claimed.

Purpose: let the paired runtime distinguish proven logical payload bounds from
state that requires external storage before bar zero. Failure shape: treating
an initial size, input default, fixed handle, or history horizon as a lifetime
payload bound; accepting a metadata-only change as a real storage migration.
Check: executable generated metadata, compile/run probes, engine-enabled full
pytest, corpus compile sweep and CPython/Pyodide gates, all through lab remote.

Final repaired-source job: `rj-20261021t005358-08f989`, clean codegen
`464cc174bc1cd35346000f27a9bfd422c295c4a7`, tree
`2859390aad1388c5138868b4c5c1cb5ef73f8c1a`, same engine/corpus pair below.
Steps: pytest, compile-corpus, gate, pyodide. **QUEUED, no host/result yet.**
The normal pick reports every NTU2, NTU and CLOUD slot occupied. No host
capacity or account override was used. This job remains **UNVERIFIED**.

## Source audit

References below are to this codegen repository at the delivered source tree.
“External required” describes the compiler requirement; the emitted storage
operations remain resident unless the row explicitly says otherwise. The
inventory is derived from the emitted persistent declarations, including clones
and caches (`pineforge_codegen/codegen/base.py:5381`,
`pineforge_codegen/codegen/state_bounds.py:180`). Transient scratch is audited
here but is not falsely represented as a persistent member.

| Container / state | Compiler evidence (file:line) | Provable bound | Emitted requirement / present implementation |
| --- | --- | --- | --- |
| Pine arrays | `pineforge_codegen/codegen/types.py:182`; constructors `pineforge_codegen/codegen/visit_call.py:2158`; append `pineforge_codegen/codegen/tables.py:815` | Constant constructor cardinality only when every use preserves cardinality, with no escape or rebind (`pineforge_codegen/codegen/state_bounds.py:139`). Inline primitive element size uses target `sizeof`. Appending each bar has no proven bound. | Query says preallocate for the proven subset; otherwise external required. Actual members/constructors/mutations are still `std::vector<T>`. |
| UDT records and arenas | Handle/record emission `pineforge_codegen/codegen/base.py:4519`; deque `pineforge_codegen/codegen/base.py:4593`; create `pineforge_codegen/codegen/base.py:4642` | A scalar-only record has a fixed inline size; a fixed type does not bound object count or reachable children (`pineforge_codegen/codegen/state_bounds.py:209`). | External required for the arena. Actual arena remains a deque. IDs now use its native index width, without the former explicit 31-bit refusal. |
| UDT array fields / nested ownership | `pineforge_codegen/codegen/base.py:98`; `pineforge_codegen/codegen/types.py:219`; `pineforge_codegen/codegen/types.py:298` | Bar-local field analysis can establish an alias to a persistent array; it does not establish a maximum array length. Other fields retain vectors. Nested strings/collections do not become fixed payloads merely by being fields. | Arena/owning payloads require external storage; existing alias/value wrappers retained. Field liveness/root-slot generation for a Store graph is outstanding. |
| Matrices | Type selection `pineforge_codegen/codegen/types.py:186`; method dispatch `pineforge_codegen/codegen/tables.py:1092` | Constant rows times columns with only cardinality-preserving uses; indirect element payloads still unknown (`pineforge_codegen/codegen/state_bounds.py:247`). Growing/escaping matrices have no proof. | Query preallocate/external requirement; existing `PineMatrix` / `PineGenericMatrix` calls retained. |
| Strings, including strings in collections | `pineforge_codegen/codegen/types.py:172`; replacement/repeat scratch `pineforge_codegen/codegen/tables.py:1246`, `pineforge_codegen/codegen/tables.py:1251` | Immutable literal UTF-8 bytes can be bounded. `sizeof(std::string)` does not bound its payload. A fixed string-array length alone is insufficient (`pineforge_codegen/codegen/state_bounds.py:254`). | Literal members publish a byte bound; other strings require external storage. Actual strings and string operations remain STL. |
| Maps and map values containing UDTs/collections | `pineforge_codegen/codegen/types.py:184`; snapshot traits `pineforge_codegen/codegen/emit_top.py:691` | No whole-map cardinality/key-payload/value-graph proof is implemented; fixed key/value type alone is insufficient. | External required (`pineforge_codegen/codegen/state_bounds.py:265`); existing `PineMap` operations and identity-preserving snapshots retained. |
| Numeric/boolean history and `max_bars_back` | `pineforge_codegen/codegen/base.py:2071`; suffix `pineforge_codegen/codegen/base.py:2101`; declarations `pineforge_codegen/codegen/base.py:4963` | An explicit emitted numeric horizon bounds slots; inline primitives have target sizeof. No directive means the existing engine default, not a new inferred compiler bound (`pineforge_codegen/codegen/state_bounds.py:231`). | Explicit horizons publish preallocation evidence. Existing `Series<T>` constructors retained. Per-variable directives are still combined by the existing maximum rule. |
| Array/matrix history snapshots | Freeze `pineforge_codegen/collection_history.py:238`; slots `pineforge_codegen/collection_history.py:383`; capacity `pineforge_codegen/codegen/collection_history.py:200` | Slot count can be fixed by a literal offset/directive. Each slot can still hold a growing frozen collection. | External required for variable payloads; existing Series of shared frozen copies retained. Slot bounds are never reused as array-length bounds. |
| UDT undo log | `pineforge_codegen/codegen/base.py:4536`; first-touch capture `pineforge_codegen/codegen/base.py:4604` | Number/size of touched record snapshots is unproven. | External required; existing vector of closures remains. Cannot serialize closure or vector object bytes to Store. |
| Full script checkpoint / fixed tuple of state | `pineforge_codegen/codegen/emit_top.py:906`; vector snapshots `pineforge_codegen/codegen/emit_top.py:782`; generic matrix snapshots `pineforge_codegen/codegen/emit_top.py:738` | The tuple has a fixed number of fields; indirect snapshots do not have a fixed payload. Primitive-only checkpoints have a complete inline sizeof proof (`pineforge_codegen/codegen/state_bounds.py:270`). | External required when indirect payloads exist; existing snapshot/restore remains. No approximate or partial rollback introduced. |
| Security helper maps / string history | `pineforge_codegen/codegen/base.py:4902`; existing limitation `pineforge_codegen/codegen/base.py:1121` | No emitted cardinality proof; helper series constructed through map access retain the prior default horizon. | External required; map construction is unchanged. A larger directive is still not propagated to these helper-map entries. |
| Lower-timeframe arrays | `pineforge_codegen/codegen/base.py:4809`; per-chart-bar clear `pineforge_codegen/codegen/security.py:6167` | Number of returned sub-bars depends on run feeds/registration, not a compiler-proven constant. | External required; still vector result buffers. Clearing the preceding chart bar's result is its existing result lifecycle. |
| TA objects, dynamic/simple lengths | `pineforge_codegen/codegen/ta.py:1473`; plans `pineforge_codegen/codegen/ta.py:1523`; types `pineforge_codegen/codegen/ta.py:1839`; runtime rebuild `pineforge_codegen/codegen/base.py:6079` | A constructor length does not by itself prove all allocations in an opaque runtime type. First-call-bound/simple and per-call series lengths cannot be guessed from defaults. | Opaque objects require external-storage evidence from the runtime. Existing `FirstCallBound`, series-extrema and reset paths retained; no new TA refusal. |
| Finite-choice extrema lengths | `pineforge_codegen/finite_ta_length.py:608` | The pass creates fixed variants for admitted choices, preserving selection. This does not bound other script collections. | Existing variants retained; no capacity cap or approximate extrema/quantile substitution. |
| TA precalculation caches | Member `pineforge_codegen/codegen/base.py:4935`; resize `pineforge_codegen/codegen/emit_top.py:2714`; checkpoint exclusion `pineforge_codegen/codegen/emit_top.py:522` | The caches are sized by run bar count, not a compile-time bound independent of bars. | External required; existing immutable per-run vectors remain. |
| Drawing arenas / collection scratch | `pineforge_codegen/codegen/base.py:5306`; `pineforge_codegen/codegen/drawing.py:61`; `pineforge_codegen/codegen/drawing.py:82` | Live drawing limits do not bound all historical IDs or record payloads. Collector scratch depends on excess records. | No new capacity/eviction. The existing Pine drawing lifetime collector remains; removing its semantic deletions would change Pine behavior. Actual external drawing adapters outstanding. |
| Array aggregation and expression scratch | `pineforge_codegen/codegen/tables.py:861`, `pineforge_codegen/codegen/tables.py:863`, `pineforge_codegen/codegen/tables.py:873` | Copies/sort-index vectors are as large as the input array. No independent bound exists for a growing input. | Existing exact copy/sort/quantile algorithms retained. Transient external scratch integration outstanding. |
| Run lifecycle reset | `pineforge_codegen/codegen/emit_top.py:582` | Resets reconstruct resident members and invalidate prior checkpoints/caches. Reserving in the strategy constructor alone would not survive this reset or a first-bar vector move assignment. | No ineffective reserve-only preallocation claim; actual pre-bar allocation needs a typed lifecycle-aware adapter. |

## Exact generated changes

- New read-only `strategy_script_state_bounds_v1(size_t*)` and its static
  metadata table (`pineforge_codegen/codegen/state_bounds.py:282`), emitted
  after the complete generated class (`pineforge_codegen/codegen/base.py:5459`).
  It is callable before `strategy_create`; unknown flags accompany zero fields.
  Every element-byte proof is evaluated by the target C++ compiler.
- UDT handle declaration/na detection and arena create/get use `std::size_t`
  (`pineforge_codegen/codegen/base.py:4522`,
  `pineforge_codegen/codegen/base.py:4642`). Existing stable-record deque,
  aliasing, na access and checkpoint topology are retained.
- No array, matrix, string, map, history, TA or snapshot allocation/mutation
  call site has been redirected to `detail::o1::Store`. That part of the brief
  is **not implemented**, and is not hidden by a successful metadata test.

## Engine dependency and assumed contract

The requested `core-cr150-p00/engine` had an empty corpus and lacked
`include/pineforge/detail/o1_store.hpp`. The selected populated checkout is
`/Users/haoliangwen/.local/state/pineforge/runner/SM-CX/proofs/canary-on/wt`,
engine `a41b26b96178c1bb6653c380f96aac8def4912dc`, corpus
`e99bbd9db257740d9c77db37a8567616627130bf`; lab submit recorded both.

The frozen A5 header was read at
`../core-cr150-a5store/engine/include/pineforge/detail/o1_store.hpp`
(relative to the lane root). Its Store byte API and Tx/Backing
obligations are recorded in `docs/SCRIPT_STATE_BOUNDS.md:57`. No declarations
were invented or modified. The populated verification engine does not contain
that header; it validates current runtime compatibility, not an A5 Store link.
The in-progress A5-UDT `detail/udt_graph.hpp` provides scalar byte access,
edge IDs, root slots and safe-point collection, but is not a typed replacement
for the emitted reference-bearing records or collections. It has not been
overlaid into another lane or represented as verified integration.

Typed prototypes appeared during this audit: A5-ARR `array.hpp` has
`ScriptArray<T>::preallocated` and Store constructors, A5-STR has a Store-scoped
`ScriptString`, and A5-UDT has the graph prototype. Their inspected interfaces
and the emitter compatibility gap are recorded in
`docs/SCRIPT_STATE_BOUNDS.md:73`. They are not in the populated verification
engine, and no untested feature branch is represented as integrated storage.

Needed from the engine-side integration: typed collection interfaces, payload
serialization/alias ownership, Store/Backing lifetime for each run, graph root
and safe-point rules, checkpoint/Tx coordination, and a pre-run allocation
consumer. A request for the agreed compiler-facing API remains pending.

## Capacity and retention audit

The explicit UDT 31-bit object-ID capacity stop was removed together with the
narrow cast, rather than bypassing the check and allowing ID wrap
(`pineforge_codegen/codegen/base.py:4642`). Existing identity/na/rollback
tests now use the arena index type
(`tests/test_udt_object_identity.py:46`,
`tests/test_script_state_bounds.py:166`).

Outstanding: resident STL constructors can still report allocation/length
failures (`pineforge_codegen/codegen/visit_call.py:2183`); generation/token
exhaustion is still an engine-invariant stop
(`pineforge_codegen/codegen/base.py:4559`); default rings still retain their
existing horizons (`pineforge_codegen/codegen/base.py:2101`,
`pineforge_codegen/collection_history.py:385`); the drawing arena “unbounded”
sentinel is still finite (`pineforge_codegen/codegen/drawing.py:61`).
Removing these without a real external representation would risk overflow,
alias loss, changed history, or changed drawing behavior. They are **remaining
work**, not claimed solved. Source-requested pop/remove/clear, rollback
truncation and Pine drawing lifecycle deletion were not removed; they are
semantic operations (`pineforge_codegen/codegen/tables.py:744`,
`pineforge_codegen/codegen/base.py:4695`,
`pineforge_codegen/codegen/drawing.py:90`).

## Lab evidence

Only lab remote runs execute repository code; no pytest, compiler, build,
repository script or local-model review ran on the Mac.

| Job | Submitted source | Host / result |
| --- | --- | --- |
| `rj-20261020t235436-e4537e` | Base plus first test draft | Cancelled; the draft imported the wrong compile helper. Not fail-before evidence. |
| `rj-20261020t235505-fa98ff` | Base `52101f9b7b540fece4493ddc7495451b6d85fc07` plus tests only; tree `005d1268192525867b7bbd0716e862f3cce12e9d`; diff `08bad712bac42baab87ceccf6c930164f17221a5ff5bad99c82767c596a1f228` | CLOUD; **expected FAIL**, exit 1: **17 failed, 9878 passed, 44 skipped**. Every failure is a new bound-metadata assertion; no existing-suite failure. |
| `rj-20261021t000104-bcf674` | Initial implementation snapshot; base plus diff `6d150d51da96e558010d99aa00c1e1f9036654106a552a2d2be3c758c65a136e`; tree `b3dfbed2ee426633c48e089564af3fc3ae19756e` | CLOUD; **FAIL**: pytest **28 failed, 9867 passed, 44 skipped**; corpus **312 passed, 0 skipped**; full CPython/Pyodide gate **279 fixtures, PARITY OK**; Pyodide packaging **PASS**. This snapshot predates later repairs and the native-ID change. |

Committed-source verification: `rj-20261021t002132-e4130e`, CLOUD, clean codegen
`5ffd55324653fd9b4595e281ce39f250e248c527`, tree
`435055243987fb3f6da44a6e50bcbe00f87e9b0f`. Engine and corpus are the same
pinned pair above. Result: **FAIL**, pytest **9 failed, 9888 passed, 44 skipped**;
corpus **312 passed, 0 skipped**; full CPython/Pyodide gate **279 fixtures,
PARITY OK**; Pyodide package **PASS**. All failures were the remaining raw
scaffold comparisons, matrix receiver-keyword proof differences and the UDT
scaffold digest. Their repairs require a new run.

Fail-before log: `logs/pytest.log`, sha256
`b7776dedf70fab37700aa5487040294f9d5da59d0551ba0115c7588b7cfdeba3`.
The lab resolved engine headers, generated version.h, Eigen, runtime library
and 312 corpus strategies. Skips concern unavailable historical comparison
commits, clang inventory, empty existing parameter sets and optional population
inputs; no skip reports missing native inputs. The test draft shipped in this
base job predates the additional history/native-ID probes in the committed tree.

Fail-before tests require executable metadata, not a comment:
`tests/test_script_state_bounds.py:29` checks the growing vector/append shape
and the missing lifetime bound; `:42` requires a bound for several constant
sizes; `:66` requires external storage for mutations, rebinds, inputs and
escapes. The compiled query probe `:125` reads real target-size metadata
before creating a strategy and checks the explicit resident/external
distinction. These tests establish compiler evidence only; they deliberately
do **not** pretend to establish an external-storage implementation.

Initial-snapshot corpus sweep: **PASS**, 312 tests, none skipped.
Initial-snapshot CPython/Pyodide full gate: **PASS**, 279 fixtures
(ok 266/266, err 13), comparator/verdict selftests passed.
Initial-snapshot Pyodide package gate: **PASS**.
These results do not verify the later source tree; its run remains pending.
Repaired-source engine-enabled full suite and skips: **UNVERIFIED**, final job queued.
Touched-test collection: **UNVERIFIED** until lab output is available.

Initial full-suite failures were scaffold identity comparisons plus matrix
keyword/positional proof differences. The proof now reuses the emitter's
`_matrix_functional_receiver` (`pineforge_codegen/codegen/tables.py:1037`).
The additive query alone is removed by `tests/_legacy_cpp.py:41` when an
existing test compares pre-feature scaffold bytes; actual generated artifacts
still compile and run with the complete query. No old strategy-body golden or
trade tape is weakened by removing its body from a comparison.

Committed-run logs: pytest sha256
`66f9d52a740fb5073b2e891d308375beb91fb5fcecb571abc676cc184e4ae8ee`;
corpus `3e5373f8c343ff94b3b44898b2cfbb7617d6e9b73e0b3638d1c4b1b3b2fbfa88`;
gate `d0dbd63d42d40abaa65ae670feca12925f54d433a37fb3c75c06c3c2724b1758`;
package `9a04708555724a7eed66cab09e2e77b2392cac44a7f0fc9e34c1e6a08b1d4d36`.

The UDT scaffold pin in `tests/test_map_call_diagnostics.py:217` was harvested
from that committed lab run: old
`ef7d12496ae3c8e07e8bbf7c20efb5a3aaee2f69f786c67747d804aec57e4e45`, new
`9bc35862f65ed94cb7992b4879ef8e71ee47b2eca9caf96abc6fff0dd86a66df`.
This is the intentional native-width handle/create/get change; the existing
runtime-value assertion remains intact. No trade tape or engine hash pin moved.

Constant aliases are cached in source order
(`pineforge_codegen/codegen/state_bounds.py:72`), so a flat source alias chain
cannot introduce recursive proof traversal beyond the bounded expression AST.

## Compile audit

| Change | Declaration / access / ABI check | Verification |
| --- | --- | --- |
| `state_bounds.py` | Uses existing AST/TypeSpec interfaces; sizeof refers to emitted public member/record types; query has no strategy pointer, mutation, allocation or engine-private access. Unknown never means zero capacity. | Runtime query probe and full lab suite pending. |
| `base.py` | Inventory collected from the same declarations as lifecycle reset/checkpoint, preserving declaration order; no id()-based ordering. Native index type matches deque::size_type; na is the maximum index. | Existing identity, alias, stable-reference, history and cyclic COOF probes pending. |
| `emit_top.py` / `helpers.py` | Explicit cstddef include; metadata type/export names reserved against authored names. No host API is assumed. | Corpus compile pending. |
| `tests/_legacy_cpp.py` | Only the additive bound-query block/include is normalized out of the existing frozen strategy-body golden. Actual query output has a separate compile/run test; strategy bodies are not normalized away. | Golden and full suite pending. |
| Tests / docs / AGENTS and CLAUDE | Existing capacity-spelling expectations changed to native-width IDs; alias/rollback assertions retained; memory guidance kept in sync. No runtime dependency or VERSION change. | Lab pending; Claude review not performed. |

## UNVERIFIED / not complete

- Actual pre-bar allocation and typed external paths for every audited category.
- Full alias/liveness/root analysis and nested object/collection serialization.
- Exhaustive proofs beyond the conservative constant constructor subset (including
  fixed-key maps and fixed structured tuples not recognized as inline primitives).
- Removal of remaining STL/generation/default-ring/finite-sentinel capacity and
  retention paths through the real external runtime, preserving semantics.
- Flat resident-memory measurements, external Store linkage, exact Store Tx rollback.
- Repaired-source final lab results and touched-test collect-only; the final
  job is queued. Earlier corpus/Pyodide results above are verified only for
  their submitted trees. Claude review remains unperformed.
- No push or PR is authorized or performed. This lane must not be called ready
  on the basis of this metadata-only milestone.

