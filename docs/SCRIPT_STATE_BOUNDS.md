# Compiler evidence for script-state allocation

The generated translation unit exports this query without constructing a
strategy or running a bar:

```cpp
struct _PFScriptStateBoundV1 {
    const char* member;
    const char* kind;
    bool cardinality_known;
    uint64_t cardinality_bound;
    bool element_bytes_known;
    size_t element_bytes;
    const char* storage_requirement;
    const char* current_storage;
};
extern "C" const _PFScriptStateBoundV1*
strategy_script_state_bounds_v1(size_t* count);
```

The pointer and its strings have translation-unit lifetime. `count` may be
null. The query allocates nothing and changes no state. Rows follow generated
member declaration order, including callable clones, TA objects, precalculated
caches, history storage and the rollback checkpoint. It does not inventory the
inherited engine's broker state or transient expression scratch storage.

A known cardinality is an upper bound on logical elements throughout a run.
A known element size bounds the complete inline logical payload of one element;
it excludes allocator/container bookkeeping, which the runtime must size for its
own representation. The C++ compiler evaluates `sizeof` for the target ABI.
When a known flag is false, its numeric field is zero and means unknown. A
known cardinality of zero means a proven empty container.

`storage_requirement` is `preallocate` only when both proofs exist; otherwise
it is `external`. `current_storage` says where the member actually lives today:
the literal `resident` for every category still held in STL/engine storage, and
the emitted `_PF_O1_CURRENT_STORAGE` expression for a member whose backing
depends on the engine the translation unit is built against. This is
allocation evidence plus the storage actually emitted; it is not a claim that
the whole script is flat-memory resident.

The proof pass considers a uniquely named top-level array/matrix initialized
from constant cardinality/dimensions, provided every use is a known operation
that cannot increase cardinality. Growing operations, assignments, aliases,
user-call arguments, shadowed names and unknown methods invalidate the proof.
Numeric constructor constants use a conservative exact integer subset, including
earlier immutable constants. Input defaults, runtime lengths, floating arithmetic
and user-function results are not treated as compile-time bounds.

Inline primitives have target `sizeof` evidence. A literal immutable string has
a byte bound. A fixed array of strings still has unknown per-element payload.
A UDT's fixed record/handle layout does not establish the size of its reachable
object graph. A collection-history horizon bounds the number of slots, not the
collections frozen in those slots. Opaque runtime TA objects retain unknown
payload bounds even if their constructor has a length parameter; a length is
not assumed to bound every allocation inside that runtime type.

## The precalculated TA cache is routed

`codegen/o1_store.py` emits `_PFO1Buffer<T>`: a typed view of one store object
(`create` at `resize`, element read/write through `read`/`write`, `size`), and
`_PFO1StoreOwner`, which opens `Store` over `make_memfd_backing()` with a fixed
`Shape` (64 pages of 4096 bytes; `_PF_O1_PAGE_BYTES` / `_PF_O1_RESIDENT_PAGES`).
The cache is the one audited category the compiler owns completely: one logical
element per run bar, trivially copyable elements (`static_assert`), and exactly
`resize`, write and read. Its slot count is a run value, so it cannot be
preallocated; the store never refuses for capacity and spills beyond the
resident window.

The binding is feature-detected at compile time. `__has_include` selects the
store when the paired engine carries the header; otherwise — and in any TU
built with `-DPF_O1_STORE_DISABLED` — the same interface is
`std::vector<T>`, the lowering every earlier build emitted. That fallback is
the reference: the same generated source built both ways must report identical
trades, net profit and per-bar broker-state hashes
(`tests/test_script_state_store.py`). `strategy_script_state_store_stats_v1`
reports the engine's four counters (live, resident, spilled pages, backing
bytes) and returns 0 before the store is opened.

The emitted region is delimited (`// >>> pf-o1-store` … `// <<< pf-o1-store`);
`tests/_legacy_cpp.without_state_bounds` removes it, its export, its owner
member and its container spelling from *cross-build* scaffold comparisons
only. The store emission and its runtime parity have their own tests.

## Remaining categories

`include/pineforge/detail/o1_store.hpp` defines `Store::open`, `create`,
`release`, `alive`, `size`, `resize`, `read`, `write`, `begin`, and `stats`,
with `Tx` commit and rollback and caller-provided `Backing`. It does not define
typed array, string, matrix, map or UDT adapters, payload serialization, a
compiler-bound registration API, or ownership of the backing during a script
run. Those interfaces are necessary to migrate the remaining
typed/reference-bearing C++ without guessing declarations, copying nontrivial
object bytes, or changing alias/rollback semantics. Until they land, the
remaining categories keep resident storage and report `resident`.

No capacities, evictions or approximations are introduced. UDT handle IDs now
use the arena's native index width, removing its previous explicit 31-bit
capacity refusal. Invalid/na handles still stop at the same access boundary.
This does not remove the underlying resident STL containers' allocation limits.

During the audit, other A5 lanes began publishing typed prototypes. These are
outside the populated verification engine used for this change:

- A5-ARR `include/pineforge/array.hpp:350` provides
  `ScriptArray<T>::preallocated(capacity)` and its `:357` constructor takes a
  `Store&`; `:441` / `:444` provide checked get/set, and `:649` defines a
  transaction scope. Its value API does not supply the STL iterator/index
  interface the current emitter uses for sorting, slices and aggregates
  (`codegen/tables.py:815`, `:861`, `:873`). It therefore requires actual
  operation lowering and lifecycle integration, rather than a type alias.
- A5-STR `include/pineforge/script_string.hpp:20` provides a Store scope and
  `:38` defines `ScriptString`; the generated strings and helper calls are
  still the `std::string` path (`codegen/types.py:172`, `codegen/tables.py:1246`).
- A5-UDT `include/pineforge/detail/udt_graph.hpp` defines byte/scalar access,
  owning edges, root slots and safe-point collection. The compiler still needs
  field encodings, live-root registration and callback/rollback coordination.

None of these prototypes has been overlaid into another lane or claimed as a
verified implementation of this compiler's external-storage requirements.
