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
it is `external`. `current_storage` is presently `resident` for every row.
**This is allocation evidence, not a typed store binding.** No existing array,
matrix, string, map or snapshot call is redirected to external storage by this
query. No engine consumes this new query yet. It cannot establish flat resident
memory or pre-bar allocation on its own.

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

The A5 engine contract inspected for this work is
`include/pineforge/detail/o1_store.hpp`: `Store::open`, `create`, `release`,
`alive`, `size`, `resize`, `read`, `write`, `begin`, and `stats`, with `Tx` commit
and rollback and caller-provided `Backing`. It does not define typed array,
string, matrix, map or UDT adapters, payload serialization, a compiler-bound
registration API, or ownership of the backing during a script run. Those
interfaces are necessary to migrate the current typed/reference-bearing C++
without guessing declarations, copying nontrivial object bytes, or changing
alias/rollback semantics. The engine and codegen must agree on them before this
metadata can drive a storage migration.

No capacities, evictions or approximations are introduced. UDT handle IDs now
use the arena's native index width, removing its previous explicit 31-bit
capacity refusal. Invalid/na handles still stop at the same access boundary.
This does not remove the underlying resident STL containers' allocation limits.
