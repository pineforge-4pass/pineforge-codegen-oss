## Conventions

- **Type system.** `PineType` (in `symbols.py`) is intentionally small
and aligns with TradingView's primitive types. For collection /
composite types use `TypeSpec`. Codegen's `_infer_type` returns a C++
type STRING (e.g. `"std::string"`, `"PineMatrix"`) — that's what most
emission paths consume. The symbol table keeps one name per scope and a
user function lives in the global scope typed by its return, while Pine
keeps functions and variables apart: `types._variable_symbol` resolves a
variable spelled like a user function in its own function's scope
(`tests/test_e2e_function_local_name.py`).
An untyped function parameter takes each written call's type, as
TradingView compiles such a function once per argument type: a plain
function whose untyped scalar parameter receives two primitive families
at its written calls (anything but an int where the shared body holds a
float) is emitted once per call site like a stateful one, each variant
typed from its own call, and so is a function it forwards that parameter
to (`_untyped_param_family_conflicts`). One body typed from the first
call narrowed a later float into an `int` (`s(2.5)` read "2"). Two
textual calls one variant shares keep the first type and warn. Each such
function is emitted once per call path through the functions it makes
stateful, so where the copies would outgrow the script, more than 64 and
four per written call in all (a diamond of forwarding helpers: 2**depth
copies of its leaf; a deep forwarding chain called many times), the
function gaining the most copies and the functions it is reached from keep
one body each, typed from the first call, and warn; flat calls, forwarded
or not, keep their variants (`_bounded_family_polymorphism`;
`tests/test_e2e_untyped_param_families.py`, tape
`fixtures/silent2_tv/cgs2_untyped_params`).
- **Errors.** Use `errors.CompileError` for fatal issues raised from
the transpiler. Carry `SourceLocation` so users can map back to the
Pine line/col. Diagnostics inside the support checker use
`Level.WARNING` for divergences-but-not-broken, `Level.NOTE` (`_note`)
only for the eleven informational codes named under `support_checker`
above, `Level.ERROR` otherwise.
- **UDT array fields.** TradingView holds the array a UDT field is built
from, and keeps a `var` array's value per bar: a push through `h.xs`
reaches `a`, while an object kept from an earlier bar reads the array as
it was then. A type none of whose objects outlives its bar
(`_udt_bar_local_types`: no `var` declaration, collection, other type's
field or history read holds one; a `var` holding a value that can be an
object of a type this cannot tell, a user function's result such as
`pick(o) => o`'s among them, keeps every type) stores its array fields as
`_PFArrayField<T>` (`base.UDT_ARRAY_FIELD_CPP`): an alias of a top-level
`var` array no statement rebinds, else the value moved or copied in;
reads dereference it, an assignment rebinds it. Every other type keeps
the `std::vector<T>` copy it always stored. A record built each bar
copied its growing arrays and the arena keeps every record: memory grew
with the square of the bars (`tests/test_e2e_udt_array_fields.py`).
- **TA tuple request helpers.** A helper whose value is a
`request.security` of a TA tuple (`htf() => request.security(t, "D",
ta.macd(...))`) returns the request's stored result struct
(`ta::MACDResult`, ...: `_security_helper_request_struct`, the chart's
`_ta_return_type`), which
`[m, s, h] = htf()` decomposes; it returned a `double`, which did not
compile (`tests/test_e2e_ta_tuple_request_helper.py`).
- **Methods on scalars.** A typed method called on a script scalar
(`s5.m()` over `s5 = ta.ema(close, 5)`) resolves through the receiver's
own type (`_scalar_receiver_spec`): a global scalar's UDT tombstone hid
it, and the call was emitted raw, a member call on a double, on the chart
and in a payload (`tests/test_e2e_scalar_method_global.py`).
- **Array index loops.** `for [i, v] in <array>` binds the array once and
indexes it (`__pf_array_iter_N` / `__pf_array_index_N`), the index an
`int`, the element a copy of its value (`value_type`: a `std::vector<bool>`
element read through `auto` is a proxy a later `arr.set(i, ...)` in the
body changes; tape `fixtures/silent2_tv/cgs2_array_loop_bool_copy`); it was
a structured
binding over the vector, which only a map's pairs admit, and did not
compile (`tests/test_e2e_array_index_loop.py`).
- **Unary signs.** A never-reassigned numeric name is inlined as its
literal, so a sign's operand text can start with one: `unary_sign_cpp`
(`codegen/helpers.py`) parenthesizes it there (`-NEG` over `NEG = -5` is
`(-(-5))`; it was `(--5)`, a decrement of a literal), on the chart and in
a `request.security` payload (`tests/test_e2e_negated_constants.py`).
- **Comments in emitted C++.** When emitting a fallback / unsupported
stub, include a `/* unsupported: ... */` marker in the source so a
later compile error has context. Avoid emitting bare empty literals.
A string a script spells reaches the C++ only as a string literal's
value (`_cpp_string_escape`) or as comment text (`_cpp_comment_escape`):
escape every script-derived value where it is written into a literal or
a comment, a validated one too (`tests/test_emitted_string_literals.py`).
The string escape spells NUL as a fixed three-digit octal escape. The tests
check that benign values actually reach their emitter and that accepted samples
round-trip through the C++ literal; intentionally un-emitted metadata is separate.
`syminfo.prefix(symbol)` and `syminfo.ticker(symbol)` read their positional or
keyword argument, splitting at the first colon. A supplied prefix is empty
without a colon or with no ticker after it; a ticker without a colon is the
whole string. Variable forms, no-argument calls and direct chart-ticker-ID calls
keep their prior chart lowering: the prefix helper preserves a prefix-less
chart ID, unlike the general argument path. Non-string lowered arguments keep
compiling and yield an empty string, including the existing string-na typing gap.
- **Helper underscores.** Codegen-internal helpers in `codegen/tables.py`
are underscore-prefixed (`_matrix_add_row`, `_merge_kwargs`); they
are not part of the package's external surface.
- **Type arguments.** A template argument and a type hint parse as the type
grammar only. `array.new<T>` and `array.from<T>` accept a Pine primitive, a
drawing/visual type, an enum the script declares, or a declared UDT; anything
else is refused before generation (and again in the emitter, because a switch
arm downgrades the checker's error to a warning). A nested element type's
inner name is not validated; an undeclared one becomes `double` and writes no
raw text. A call on a user type left after its constructor, `copy` and typed
methods, an unknown method outside its receiver's own table, and a member read
on a non-user-type value are refused instead of emitted. A `chart.point` read
of `time` / `index` / `price` stays allowed; a receiver the resolver cannot
place keeps the historical pass-through for a Pine table method name.
- **Reserved names.** `codegen/helpers.py::CPP_RESERVED` carries every C++17
and C++20 keyword/operator alternative plus header macros and emitter names
that can collide. `_safe_name` allocates distinct escapes against all authored
spellings; UDT fields use the same mapping. It also carries
`codegen/host_members.py::HOST_MEMBER_NAMES`, the host members the generated
class reads or writes unqualified (a script variable `session_isfirstbar_`
used to become the value of `session.isfirstbar`, and one named
`current_bar_` broke the compile). Never edit that set by hand:
`scripts/gen_host_members.py` derives it from clang's AST of the host header
the emitted C++ includes and the identifiers the emitter's string constants
spell, and `tests/test_host_member_names.py` regenerates it and checks it
against every host member a transpiled battery names. TradingView accepts
such names and keeps each built-in beside them: a US-stock tape kept in a
private repository is replayed bar for bar. Rerun the script after a lowering
starts reading a host member or the
engine's host changes. The temporaries the emitter declares in the C++ around
a user's expression (`_nz_v`, `_pna_l`, `_hv`, `_v0`, `__switch_val_N`,
`_tuple_unused_N`, ... and every name in its `_pf` / `__pf` namespace:
`helpers.is_emitter_temporary`) are escaped too, so a script name can neither
capture nor be captured by one; a new template local belongs in the `_pf_`
namespace (`tests/test_e2e_temporary_name_hygiene.py` derives the set from
emitted C++).
- **Input limits.** `limits.py` turns a crash or a hang into a located
`CompileError`; where TradingView documents a limit, ours is at least as
large. 5 MiB of source (TradingView's 5MB compilation request), 512 levels
of nesting (brackets, blocks, prefix operators, `?:`/`else if` chains and
syntax-tree depth; TradingView documents none) and a cooperative 120-second
guard (its two-minute compile limit). There is no statement-count or
statement-size budget: none guarded a crash, and TradingView counts compiled
tokens. The parser bounds the tree it builds, operator chains included,
because freeing a tree about 4,000 levels deep overflows Pyodide's stack
(fatal); `ensure_recursion_headroom` raises Python's recursion limit to 40
frames per level and never lowers it. A per-item loop whose body scans the
whole script needs its own `self._budget.check` (the `var`-member scans in
`codegen/base.py`), and `FuncCall.annotations["call_arg_order"]` is an
`ArgOrder`, which the generic AST walkers do not enter: re-walking the
aliased arguments cost 2**depth. A walk that follows user calls must not
visit a helper once per call path: a diamond of helpers (`f1(x) => f0(x) +
f0(x)`, 22 levels) has 2**22 paths. The map-history validation and a
payload's mutable-global scan walk each body once (analyzer), the known-value
spelling spells a call of the same arguments once (`_arith_expr_to_str`), and
a payload's walks stop at a pure call (see "`request.security` helpers").
Across the 325 public corpus sources and
277 gate fixtures, maxima are 9,869 characters, nesting 10 and 0.05 s
(2026-09-29, main 70c2b4a, CPython 3.14 on an Apple M4 Max; 0.03 s on
2026-09-26).

