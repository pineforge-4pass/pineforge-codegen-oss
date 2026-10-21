# A5-CGEN — compiler script-state bounds and the external store

**Status: PARTIAL, honestly scoped, green.** Final verification
`rj-20261021t043103-05daf5` (codegen `02b3c326`, engine `c47e51d8a` + the
three-line store fix) passed every step: pytest 9904 passed / 44 skipped,
corpus 312 passed, gate and Pyodide PARITY OK. The precalculated TA cache — the one
audited container category whose storage the compiler owns end to end and
whose payload is trivially copyable — now lives in the engine's O(1) store
(`pineforge/detail/o1_store.hpp`): a fixed resident working set over a
spilling backing, stable ids, no capacity refusal. Every other category
(arrays, matrices, strings, maps, UDT records and their undo log, collection
history slots, the rollback checkpoint, TA objects, drawing arenas,
lower-timeframe buffers) still reports and uses resident STL/engine storage;
no row is claimed complete on the strength of compiling.

Purpose: let the paired runtime distinguish proven logical payload bounds from
state that requires external storage before bar zero, and route the state the
compiler cannot bound. Failure shape: treating an initial size, input default,
fixed handle, or history horizon as a lifetime payload bound; accepting a
metadata-only change as a real storage migration. Check: executable generated
metadata, compile/run probes, engine-enabled full pytest, corpus compile
sweep and CPython/Pyodide gates, all through lab remote.

## The one category routed: the precalculated TA cache

`codegen/o1_store.py` emits, only for a script with a precalculated TA cache:

- a compile-time guard (`__has_include(<pineforge/detail/o1_store.hpp>)`, and
  `-DPF_O1_STORE_DISABLED` to force the fallback);
- `_PFO1StoreOwner`: opens `Store::open(Shape{4096, 64}, *make_memfd_backing())`
  lazily, and reports the engine's four counters;
- `_PFO1Buffer<T>`: a typed view of one store object — `create` on first
  `resize`, element read/write through `Store::read`/`Store::write`, `size`,
  `static_assert(std::is_trivially_copyable<T>)`. A store failure throws; it
  never refuses for capacity, because the store does not;
- `strategy_script_state_store_stats_v1(handle, out[4])`, after the complete
  strategy class, returning live/resident/spilled/backing counters (0 before
  the store opens).

The cache member changes from `std::vector<T> _precalc_<site>;` to
`_PFO1Buffer<T> _precalc_<site>{&_pf_o1_store_};`. The lifecycle reset repeats
that initializer, so each run rebinds the view to the strategy's owner.

Without the engine header — and in any TU built with `-DPF_O1_STORE_DISABLED` —
`_PFO1Buffer<T>` is a `std::vector<T>` behind the same interface. That fallback
is the reference lowering: it is byte-for-byte the container every earlier
build emitted, so the store path cannot change strategy results, and the two
builds of one emitted source are required to report identical trades, net
profit, equity and per-bar broker-state hashes.

Scoping: a script without a precalculated cache emits none of this
(`_PFO1Buffer`, `_PFO1StoreOwner` and the export are absent), so every other
lowering keeps its bytes.

The bound query's row for such a member now carries
`current_storage = _PF_O1_CURRENT_STORAGE`, which expands to `"external"` when
the TU is built against the store and `"resident"` otherwise. The query's
other rows keep the literal `resident`; the public row value is still the
plain word, and the emitter emits a quoted literal, and an emitted `_PF_` macro name as an expression.

### Engine defect found in the store commit

`c47e51d8a`'s `src/detail/o1_store.cpp` inverts the overflow test at three
sites: `add_overflow` returns **true on overflow**, and `create()` /
`resize()` are written `if (!add_overflow(...)) throw ...`. Every `create`
therefore throws `o1 store: backing offset exhausted`. The first lab run of
this change (job `rj-20261021t012714-82fc2f`) hits exactly that: 268 failures,
every E2E run rejected with that message. Verification of this lane therefore
uses a companion engine carrying the three-line polarity fix
(`engine-storefix`, diff `0e54abb4fcdb`, recorded by the lab):

```diff
-  if (!add_overflow(meta_off, kMetaBytes, &data_off))
+  if (add_overflow(meta_off, kMetaBytes, &data_off))
-  if (!add_overflow(data_off, bytes, &end))
+  if (add_overflow(data_off, bytes, &end))
-    if (!add_overflow(new_off, bytes, &end))
+    if (add_overflow(new_off, bytes, &end))
```

The store lane must fix this upstream; this lane's results are **not** a
verification of the committed store as it stands. `align_up` (true on success)
is used correctly and was left alone.

## Source audit

References below are to this codegen repository at the delivered source tree.
“External required” describes the compiler requirement; the emitted storage is
listed per row. The inventory is derived from the emitted persistent
declarations, including clones and caches
(`pineforge_codegen/codegen/base.py`, `pineforge_codegen/codegen/state_bounds.py`).
Transient scratch is audited here but is not falsely represented as a
persistent member.

| Container / state | Compiler evidence | Provable bound | Emitted requirement / present implementation |
| --- | --- | --- | --- |
| Pine arrays | `codegen/types.py`; constructors `codegen/visit_call.py`; append `codegen/tables.py` | Constant constructor cardinality only when every use preserves cardinality, with no escape or rebind (`codegen/state_bounds.py`). Inline primitive element size uses target `sizeof`. Appending each bar has no proven bound. | Constant-cardinality arrays preallocate through their own constructor; every other array is reported external and **still** `std::vector<T>`. Not routed. |
| UDT records and arenas | Handle/record emission, arena template `codegen/base.py` | A scalar-only record has a fixed inline size; a fixed type does not bound object count or reachable children. | External required; **still** `std::deque<_PFSlot>`. Records carry strings/vectors/UDT handles, so a byte store needs field encodings and live-root registration. Not routed. IDs use the arena's native index width. |
| UDT array fields / nested ownership | `codegen/base.py`, `codegen/types.py` | Bar-local field analysis can establish an alias to a persistent array; it does not establish a maximum array length. | Existing `_PFArrayField<T>` alias/value wrappers retained. Not routed. |
| Matrices | `codegen/types.py`, `codegen/tables.py` | Constant rows times columns with only cardinality-preserving uses; indirect element payloads unknown. | Constant-shape matrices preallocate through their constructor; the rest report external and **still** `PineMatrix` / `PineGenericMatrix`. Not routed. |
| Strings | `codegen/types.py`, `codegen/tables.py` | Immutable literal UTF-8 bytes can be bounded. `sizeof(std::string)` does not bound its payload. | Literal members publish a byte bound; other strings report external and **still** `std::string`. Not routed. |
| Maps | `codegen/types.py`, `codegen/emit_top.py` | No whole-map cardinality/key-payload/value-graph proof. | External required; **still** `PineMap`. Not routed. |
| Numeric/boolean history and `max_bars_back` | `codegen/base.py` (series declarations, directive cap) | An explicit emitted horizon bounds slots; inline primitives have target `sizeof`. No directive means the engine default, not a new compiler bound. | Explicit horizons publish preallocation evidence. **Still** the engine's `Series<T>` with its fixed ring. Not routed (engine-side type). |
| Array/matrix history snapshots | `collection_history.py`, `codegen/collection_history.py` | Slot count can be fixed by a literal offset/directive. Each slot can still hold a growing frozen collection. | External required; **still** the ring of shared frozen copies. Not routed. |
| UDT undo log | `codegen/base.py` (coordinator, first-touch capture) | Number/size of touched record snapshots is unproven. | External required; **still** a vector of closures (not serializable to the store). Not routed. |
| Full script checkpoint | `codegen/emit_top.py` (`_PFScriptState`) | Fixed number of fields; indirect snapshots have no fixed payload. Primitive-only checkpoints have a complete inline `sizeof` proof. | External required when indirect payloads exist; **still** the snapshot/restore tuple. Not routed. |
| Security helper maps / string history | `codegen/base.py` | No emitted cardinality proof; helper series constructed through map access retain the prior default horizon. | External required; **still** `std::unordered_map`. Not routed. |
| Lower-timeframe arrays | `codegen/base.py`, `codegen/security.py` | Number of returned sub-bars depends on run feeds, not a compiler constant. | External required; **still** `std::vector<T>`. Not routed (their elements flow through the whole array-method surface). |
| TA objects, dynamic/simple lengths | `codegen/ta.py`, `codegen/base.py` | A constructor length does not prove every allocation inside an opaque runtime type. | External evidence must come from the runtime. Existing `FirstCallBound`/series-extrema/reset paths retained. Not routed. |
| **TA precalculation caches** | member `codegen/base.py`; emission `codegen/o1_store.py`; reset `codegen/emit_top.py` | **None by construction**: one element per run bar, and the bar count is a run value. The element type is trivially copyable and the operations are exactly `resize`, write, read. | **Routed.** `_PFO1Buffer<T>` over `detail::o1::Store`; spills beyond 64 resident pages; `current_storage` is `external` when built against the store, `resident` otherwise. Verified by run parity with the `std::vector` fallback. |
| Finite-choice extrema lengths | `finite_ta_length.py` | The pass creates fixed variants for admitted choices, preserving selection. | Existing variants retained; no capacity cap. Not routed. |
| Drawing arenas / collector scratch | `codegen/drawing.py`, `codegen/base.py` | Live drawing limits do not bound all historical IDs or record payloads. | No new capacity/eviction. The existing Pine drawing lifetime collector remains. The engine arena's "unbounded" sentinel is still finite. Not routed. |
| Array aggregation and expression scratch | `codegen/tables.py` | Copies/sort-index vectors are as large as the input array. | Existing exact copy/sort/quantile algorithms retained. Transient external scratch not implemented. |
| Run lifecycle reset | `codegen/emit_top.py` | Resets reconstruct resident members and invalidate prior checkpoints/caches. | The store view is rebuilt (and rebound) by the same reset; the store itself survives the run. No reserve-only claim. |

## Exact generated changes

- New `codegen/o1_store.py`: the guard, `_PFO1StoreOwner`, `_PFO1Buffer<T>`, the
  stats export, and the scoping predicate. Emitted before/after the strategy
  class respectively; delimited by `// >>> pf-o1-store` … `// <<< pf-o1-store`.
- `codegen/base.py`: emit the helper only for a script with a precalculated
  cache; declare `_PFO1StoreOwner _pf_o1_store_;` before the checkpointed
  declaration block (it holds no Pine value); declare each cache as
  `_PFO1Buffer<T> _precalc_<site>{&_pf_o1_store_};`; emit the stats export after
  the class.
- `codegen/state_bounds.py`: classify `_PFO1Buffer<T>` as `precalc`, external,
  element `sizeof(T)` for an inline primitive, `current_storage =
  _PF_O1_CURRENT_STORAGE`; the emitter emits a quoted literal and only an emitted `_PF_` macro name as an expression
  as an expression. Every other row and value is unchanged.
- `codegen/helpers.py`: reserve the new emitted identifiers against authored
  names.
- `tests/_legacy_cpp.py`: `without_state_bounds` additionally removes the store
  view, owner member, export and the cache's storage spelling from *cross-build*
  scaffold comparisons (a comparison against another commit's codegen cannot
  carry a feature it predates). Nothing in the strategy body is rewritten, and
  the store emission has its own tests.
- `tests/test_script_state_store.py`: new; emission, scoping, metadata row,
  both-branch compile, and a 60,000-bar run.
- Pins retargeted to the new container spelling, every one still asserting the
  same lowering decision: `test_codegen_validation_fixes.py`,
  `test_lazy_source_clock.py` (negative pins became bare-name absence, which is
  stricter), `test_lazy_edge_ta_every_bar.py`, `test_script_run_prepare.py`.
- Docs: `CLAUDE.md`, `AGENTS.md`, `docs/SCRIPT_STATE_BOUNDS.md`.

## Verified behaviour (tests)

All of the following ran green in `rj-20261021t043103-05daf5` against engine
`c47e51d8a` + the three-line store fix (companion diff `0e54abb4fcdb`).

`tests/test_script_state_store.py`:

- the cache member is `_PFO1Buffer<double>` and no `std::vector<double>
  _precalc_` remains; the view names `Store::open` and `make_memfd_backing`;
- a script without a cache emits none of the store scaffolding;
- the export sits after the class;
- the fallback branch is the `std::vector<T>` lowering;
- the emitted TU compiles against the paired engine in both branches
  (`-fsyntax-only`);
- the bound row is `precalc` / external / `sizeof(double)` /
  `_PF_O1_CURRENT_STORAGE`;
- a 60,000-bar run (960,000 logical cache bytes, past the 262,144-byte resident
  ceiling) must spill, keep `bytes_resident` within the fixed working set, hold
  every element live, and print a byte-identical report to the same source built
  with `-DPF_O1_STORE_DISABLED`.

## Lab evidence

Only lab remote runs execute repository code; no pytest, compiler, build,
corpus run or script ran on the Mac.

Baseline (previous lane state, `464cc174`, engine `a41b26b9`):
`rj-20261021t005358-08f989` — pytest **9897 passed, 44 skipped, 0 failed**
(0 errors), corpus **312 passed, 0 skipped**, gate **279 fixtures, PARITY OK**,
Pyodide packaging **PASS**. This also settles the previously pending
collect-only question: the touched modules collect with zero errors.

| Job | Tree | Result |
| --- | --- | --- |
| `rj-20261021t012714-82fc2f` | codegen `ed370651`, engine `c47e51d8a` unmodified | **FAIL** 268 failed / 9637 passed / 35 skipped / 8 errors. Root cause: every store `create` throws (`o1 store: backing offset exhausted`). Also exposed two of this lane's own defects (a stray `\` starting the emitted block; the row value quoted). Not a codegen-semantics failure. |
| `rj-20261021t012730-0c631f` (fail-before) | lane head `0f4cc9d7` + tests only (`tests/test_script_state_store.py`, `tests/_legacy_cpp.py`, `tests/test_codegen_validation_fixes.py`, `tests/test_lazy_source_clock.py`; diff `d7a7c158fa89`) | **Expected FAIL**: **12 failed, 9892 passed, 44 skipped**. The 6 new store tests and the 6 retargeted precalc pins fail; nothing else. |
| `rj-20261021t021017-06604d` | codegen `312462fa`, engine `c47e51d8a` + the three-line polarity fix (diff `0e54abb4fcdb`) | **FAIL** 2619 failed / 7151 passed / 135 errors; corpus 312 failed. One of this lane's own defects: the emitter wrote the literal `resident` unquoted into the bound table, so every store-bearing TU failed to compile (`'resident' was not declared in this scope`). |
| `rj-20261021t033044-41e72a` | codegen `1513a829`, same companion | **FAIL**: **1 failed, 9911 passed, 36 skipped**. The single failure is the new 60,000-bar probe's own driver treating an empty recorded run error as a failure. Corpus/gate/pyodide did not run (no `--keep-going`). |
| `rj-20261021t043103-05daf5` | codegen `02b3c326`, same companion, `--keep-going` | **PASS.** pytest **9904 passed, 44 skipped, 0 failed, 0 errors** (25m17s); compile-corpus **312 passed, 0 skipped**; gate **279 fixtures, PARITY OK** (ok 266/266, err 13); Pyodide **PARITY OK**, `@pineforge/codegen-pyodide@1.5.0` packed. |

The passing run's skips are the same classes the baseline reported: historical
comparison commits absent from the clone, clang AST unavailable, empty
parameter sets and optional population sources. No skip reports a missing
native input.

Cross-build scaffold normalization is exercised by
`test_codegen_ta_dynamic_length::test_constant_and_input_lengths_keep_their_cpp`
and `test_diagnostic_notes[chart_ema]` (both green in `...-05daf5`): the store
view, owner, export and container spelling are removed from the comparison;
nothing in the strategy body is.

The fail-before log is `lab remote logs rj-20261021t012730-0c631f --step pytest`
(local copy sha256 `04b7a71898bdc936b200ac1837935fd96b6cca007f1162ccad36c7495e353e00`).

## Capacity and retention audit

- No capacity refusal is generated: `_PFO1Buffer::resize` creates or resizes an
  object and the store spills; the only throw is an engine/environment failure,
  which is reported, never silently downgraded to a smaller cache.
- Unknown is never zero capacity: the precalc row reports
  `cardinality_known = false` with the numeric field 0, and its storage is the
  unbounded store.
- Remaining capacity/retention paths, explicitly **not** removed: the engine's
  `Series<T>` default ring (`max_bars_back` default), the drawing arena's finite
  `_PF_DRAWING_UNBOUNDED` sentinel, the UDT generation/token exhaustion stop,
  collection-history ring sizes, and resident STL length/allocation failures.
  Removing these needs engine-side typed adapters (A5-ARR, A5-HIST, A5-DRAW) and
  would change Pine semantics if done by capping; they are remaining work.
- Source-requested pop/remove/clear, rollback truncation and Pine drawing
  lifetime deletion are semantic operations and were not removed.

## UNVERIFIED / not complete

- The committed store itself (`c47e51d8a`) is defective as committed; this lane
  did not fix the sibling tree, and the passing run used the patched companion
  (diff `0e54abb4fcdb`). The store lane must apply the three-line fix and
  re-verify; until then no downstream lane should depend on the store as
  committed.
- The passing run is proof for `02b3c326`; that is the tree this report
  describes.
- External routing for every other audited category, and the alias/liveness/root
  analysis and nested serialization they need.
- Removal of the remaining STL/default-ring/finite-sentinel capacity and
  retention paths through a real external runtime.
- Flat resident-memory measurements; Store `Tx` rollback wiring for script state
  (the cache is rollback-exempt by design: it is rebuilt before the broker walk).
- Leak note: each run rebuilds the cache views, so a strategy executed many
  times in one process leaves earlier store ids live until the strategy is
  destroyed (`bytes_live` grows; `bytes_resident` stays bounded).
- No push or PR is authorized or performed.
