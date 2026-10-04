# Changelog

Release notes for `pineforge-codegen`. From 1.0.0 on, versions of codegen and
[`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine) are
supported as exact pairs; on the 0.x line they are independent. See the
[pairing rule](README.md#engine-pairing).

## Release note policy

- Keep a section for each released version, including prereleases. Use the exact
  tag version and release date when published; leave a planned release marked
  **Unreleased** until its tag exists.
- Summarize changes users can observe: the Python and JSON contract, emitted C++
  and engine requirements, support or warning changes, packaging, and security
  fixes. Link the merged pull requests that supply each change.
- Call out migration steps and compatibility limits explicitly. A pair change
  requires regenerated C++ and relinked strategy libraries, even when the C
  ABI number is unchanged.
- Draft from merged changes since the previous release tag, then verify against
  the release commit. A prerelease note describes changes since the preceding
  prerelease or stable tag; the final stable note consolidates the series.

## Unreleased

### Request discovery

- `transpile_full()` returns a new key, `requests`: every request site that
  reads another symbol's feed, so a host can fetch the bars a run needs before
  it starts it. Each entry gives the request's `line` and `fn`, its symbol
  (`literal`, `input` with its override key and default, `computed` with its
  value at the inputs' defaults where literals and inputs compute it, or
  `unresolvable`), its timeframe (`literal` in the engine's feed spelling,
  `chart`, `input` or `computed`), `lookahead`, `gaps` and
  `ignore_invalid_symbol`. A request lowered to `na` (its value reaches only
  plots and alerts) is not listed. See the
  [public contract](docs/PUBLIC_CONTRACT.md#request-discovery-unreleased).
- `gate/glue.py`'s `transpile_json` success envelope carries the same list
  under `requests`.
- The input manifest marks each `input.symbol` entry with `"kind": "symbol"`;
  its `title`, `type` and `default` are unchanged.

### Compatibility and migration

- Additive only. The emitted C++ is unchanged: the engine's 325 public corpus
  sources and this repository's 540 test-fixture `.pine` files transpile to the
  same C++ as before, byte for byte, each in a fresh process (the 51 refused
  before are refused with the same message).

## 1.0.1 — 2026-10-02

A patch release of translation fixes. It covers the changes merged to `main`
since 1.0.0:
[#156](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/156) and
[#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157)
change the translation, and
[#154](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/154) and
[#155](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/155)
the documentation. Codegen `1.0.1` supports only engine `v1.0.1`. Engine
`v1.0.1` changes only documentation since `v1.0.0`, so the pair keeps C ABI
version 4 and the script ABI epoch `engine_script_run_v19`.

### Compatibility and migration

- Regenerate C++ with 1.0.1 and relink it against engine `v1.0.1`'s headers
  and `libpineforge.a`. A pair change requires it, even though the C ABI
  number is unchanged.
- The Python and JSON contract is unchanged: `transpile()`,
  `transpile_full()` and `gate/glue.py`'s `transpile_json` keep their
  arguments, result keys and envelopes. No report key changes: the engine's
  JSON report keeps the keys it had with 1.0.0, `metrics.equity.sharpe_tv`
  and `sortino_tv` included.
- The engine's 325 public corpus sources and this repository's 277 gate
  fixtures transpile to the same C++ as with 1.0.0, byte for byte (the 13
  fixtures the gate expects to be refused are refused with the same message),
  and so do the 1,384 real-world strategy sources the two pull requests
  measured, transpiled in fresh processes.
- Some scripts that 1.0.0 transpiled are refused now, each with the code
  TradingView's compiler refuses it with. Among them are an array's history
  used as an operand or as a value (`a[1] + 1`, `c.push(a[1])`: CE10123),
  which 1.0.0 read, with a warning, as an element of the current array, and
  a method straight after a history read (`m[1].get(0, 0)`: CE10011), which
  is written `(m[1]).get(0, 0)`. "Refused at compile time" below lists them
  all.

### History of user-defined objects and drawings

1.0.0 emitted C++ that did not compile for the history of an object: it
declared a variable of a user-defined type or a drawing type whose history
was read `Series<double>`
([#156](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/156)).

- `obj[k]` is the reference the variable held k bars back, `na` before the
  first, and `(obj[k]).field` reads that object as it is now, as on
  TradingView, for boxes, lines and labels too. This holds for globals, `var`
  objects, block and function locals, function parameters and method
  receivers, and a drawing parameter's or receiver's history takes its
  built-in methods (`(this[1]).get_top()`).
- In a function, a method and an `if` block the history counts the executions
  of the scope. At a call site that skips bars, a function's object
  parameter keeps one slot per chart bar, as TradingView does; a typed
  method's receiver does not yet (see "Not covered" below).
- A temporary drawing passed to a drawing parameter compiles: a history read,
  a new drawing or chart point, or a call's result.
- The history of an expression whose value is an object (`o.inner[1]`,
  `f()[1]`, `(c ? a : b)[1]`) is the reference it produced at its previous
  evaluation. Below a lazy edge, such as a `?:` arm, a read that is safe to
  evaluate (a pure or constructor call over pure arguments, a ternary over
  names, a named object's field) is kept on every bar, as TradingView keeps
  it.
- `==` / `!=` compare two lines or two labels by identity, `na` included.

### History of arrays and matrices

1.0.0 emitted C++ that did not compile for the history of an array or a
matrix, such as `(a[1]).size()`, `array.size(a[1])` and `(m[1]).get(0, 0)`
([#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157)).

- `a[k]` of an array or matrix variable, top-level or a block's local, is a
  read-only copy of the collection as the variable left it at the end of its
  scope's execution k executions back: the bar k bars back for a top-level
  variable, the block's previous run for a block's local. A built-in reads
  it, `na()` tests it, a variable or a function argument can hold it, and
  `matrix.copy(m[1])` has the variable's element type. A `for...in` loop over
  `a[1]` iterates the array the variable holds now, as on TradingView.
- Two sibling blocks that declare the same name keep a history each.
- A change to the copy, to a slice of it, or through a variable or parameter
  bound to it stops the run with TradingView's runtime error RE10051. A method
  on it before the variable has a history stops the run with RE10052 for an
  array and RE10053 for a matrix.

### Int values in float fields

From [#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157):

- `Cell.new(v = bar_index)`, a non-constant int given to a `float` field of a
  user-defined type, compiles: the int converts to a double and an int `na`
  to `na`. 1.0.0 narrowed it in a braced initializer, which did not compile.
- A value that came out silently wrong: `c.v := iv`, an int assigned to a
  `float` field, stored an int `na` as -2147483648; it now stores `na`, as
  TradingView does. On the `uassign_float` tape 112 of 336 exits differed
  before.
- A bool given to a `float` field converts to 1 or 0 and is never `na`.
  TradingView refuses a bool there (CE10123 in a constructor, CE10173 in an
  assignment).

### Refused at compile time

A located `CompileError` now refuses, with TradingView's error code, what
TradingView's compiler refuses
([#156](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/156),
[#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157)):

- a field or method straight after the history operator: `c[1].v` (CE10011),
  `b[1].get_top()` (CE10010), `a[1].size()` (CE10011). The parenthesized
  `(c[1]).v` is the valid spelling;
- the history of a field: a value field (`c.v[1]`, `(c.v)[1]`) and an
  object's array, matrix or map field (`h.xs[1]`, `(h.xs)[1]`), CE10290;
- `==` / `!=` of references other than lines and labels (CE10123), and of a
  reference with `na` (CE10187);
- an array's history where a number, a condition, a string or an element is
  expected: an operator's operand, `nz`, `math.*`, a value slot of an array
  function (`c.push(a[1])`, `array.new<float>(2, a[1])`), `log.*`'s message,
  `label.new`'s text and `str.tostring` of a color array (CE10123); a scalar
  variable or field (CE10173); an `if` or `while` condition (CE10101);
  `array.from`, and `str.format` of a color array (CE10122); a bare statement
  at the top level (CE10009).

Refused by name, where TradingView compiles the script; none of these
compiled under 1.0.0:

- the history of an object or a drawing inside a `request.security`
  expression (TradingView reads the requested timeframe's objects, which
  PineForge does not keep), and the history of a `chart.point` variable whose
  fields the script changes (PineForge holds a point as a value)
  ([#156](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/156));
- an array or matrix history read whose earlier lowering did not compile,
  but for the two gaps below
  ([#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157)).

### Not covered

- A typed method's receiver at a call site that skips bars counts calls,
  where TradingView keeps one slot per chart bar: from the third call on,
  `this[2]` reads three bars further back
  ([#156](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/156)).
- The history of a function's array or matrix parameter or local, of a call's
  result and of a selection is not kept (TradingView keeps one per call). Such
  a read keeps 1.0.0's lowering where that compiled, and is refused otherwise
  ([#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157)).
- Two kinds of history read keep the C++ they had, which does not compile: an
  element function's value of a string array's history where the earlier
  lowering is kept (in a function, a method, a loop or a block `var`), such
  as `f() => str.length(array.first(s[1]))`; and the history of a variable or
  a call that the analyzer does not type as an array or a matrix (a method's
  or `matrix.row`'s new array, a selection of arrays, a tuple's element, an
  array of drawings)
  ([#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157)).
- PineForge holds no `na` array: a variable bound to a history before the
  variable has one holds an empty array where TradingView's is `na`, and one
  the script also tests with `na()` is refused
  ([#157](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/157)).
- As in 1.0.0, these do not compile: an int from `matrix.rows()`,
  `columns()`, `elements_count()` or `strategy.wintrades` given to a `float`
  field; `matrix.sum(m1, m2)` and other matrix results used without a
  declared type; a method called on an operator expression
  (`(close * 2).m()`). `str.tostring` of an array is not lowered.

### Documentation

- The README, the changelog and the docs describe the 1.0.0 release
  ([#154](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/154)),
  and the README calls no version the latest
  ([#155](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/155)).
  PyPI shows each release's README as its description, so 1.0.1's PyPI page
  shows the README of the `v1.0.1` tag; 1.0.0's keeps the one it was uploaded
  with.

## 1.0.0 — 2026-09-30

This is the 1.0 release note. It covers the changes merged to `main`
since 0.10.4 (2026-09-06), through
[#152](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/152);
[#153](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/153)
then updated the documentation. Codegen `1.0.0` supports only engine
`v1.0.0`; `1.0.0-rc.1` supports only engine `v1.0.0-rc.1`. The notes of
0.10.4 and earlier releases are on the
[GitHub releases page](https://github.com/pineforge-4pass/pineforge-codegen-oss/releases).

### Compatibility and execution

- Generated strategies use the engine's `PineStrategyHost` source layer and
  explicitly attach Pine execution policies and cap compatibility
  ([#127](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/127),
  [#128](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/128),
  [#129](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/129)).
  Regenerate C++ and relink libraries for the exact engine pair.
- Reused strategy handles reset persistent generated state before a new run
  ([#126](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/126)).
  Price-less `strategy.exit` follows the bracket-cancel command and the
  first-tick predicate uses its accessor
  ([#130](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/130)).
- A `strategy()` that omits `initial_capital`, `default_qty_type` or
  `default_qty_value` gets TradingView's Pine v6 defaults (100,000,
  `strategy.percent_of_equity`, 100); before, the engine's own defaults
  applied (1,000,000 and 1 contract)
  ([#147](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/147)).
  Such a script sizes its orders differently and can book different trades;
  declare the arguments to keep the old sizing.
- `time()` / `time_close()` with a nonzero `bars_back` or
  `timeframe_bars_back` read another bar's time through the engine host's
  `pine_time_offset`, which the paired engine provides
  ([#152](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/152)).

### Pine translation and diagnostics

- Replace Python expression execution in constant folding with bounded
  evaluation ([#125](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/125)).
  Preserve `na` when double-valued expressions enter integer slots
  ([#131](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/131)).
- Inline `input.*()` TA lengths use the same reset and override path as bound
  inputs ([#133](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/133)).
  `ta.change` receives its lookback in `compute()`, and input titles are
  escaped consistently
  ([#134](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/134)).
- Input overrides reach precalculated TA sites; `ta.valuewhen`, VWAP anchors,
  input keys, and Pine string escapes follow the validated spellings
  ([#135](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/135)).
  TA arguments are routed by signature; multiline strings, nested input keys,
  generic `input()` lengths, and formatted `log.*` calls are covered
  ([#136](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/136)).
- Bare `ta.tr`, located parser errors, TradingView numeric text,
  `str.format(formatString=...)`, string-safe trace pragmas, and the native
  floating-point contraction flag are pinned by tests
  ([#138](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/138)).
  The paired TA1 engine handles ALMA `floor`, KC `useTrueRange`, explicit
  VWAP anchors, and pivot-level anchors/developing values
  ([#139](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/139)).
- `na` follows Pine's rules wherever the generated C++ converts a value: it
  is false in `if`, `?:`, `and`/`or` and boolean parameters, an integer
  conversion of `na` stays `na`, and stored booleans and dynamic series
  indices keep it. Keltner Channel middle bands are `na` until their length
  is warm, as on TradingView. Standalone `ta.ema` warmup, collection history
  and a nullable `str.repeat` result, which the engine cannot yet represent
  exactly, now warn
  ([#140](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/140)).
- An inverted `array.fill` range leaves the array unchanged and an inverted
  `array.slice` raises TradingView's runtime error, as on TradingView; every
  `array.slice` warns that PineForge copies the slice where TradingView
  aliases it. `request.security` resolves a chart-symbol name by lexical
  scope, so a function-local rebind no longer blocks an unrelated global
  ([#140](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/140)).
  A `?:` symbol that can select another symbol warned there; see the next
  section for what it does since #148.

### Libraries and requests for outside data

- Pine library imports are inlined at transpile time: pass the sources as
  `transpile(..., libraries={"user/name/version": source})`, or let the
  script's own requests manifest pin them (`$PINEFORGE_PINE_LIBRARIES` with
  `$PINEFORGE_REQUESTS_ROOT`). Only what the script reaches is inlined, and a
  v5 library is lowered by v5's rules or refused naming the rule it would
  need ([#148](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/148)).
  An import whose alias is `ta`, `math` or `str` and that names only that
  namespace's built-ins is a no-op
  ([#146](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/146)).
  Any other import without a source is still refused.
- A `request.security` of another symbol whose value can reach a trade reads
  the feed a requests manifest pins for that symbol, and stops the run where
  its value is read when none is installed; it never reads the chart's bars in
  its place. A `?:` symbol that
  can select another symbol and whose value can reach a trade registers the
  symbol the run computes
  ([#148](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/148)).
- `request.financial`, `request.earnings`, `request.dividends` and
  `request.splits` read the per-bar series a requests manifest records, and a
  `request.security` whose expression is `request.footprint(ticks, va)` reads
  the pinned feed's delta column
  ([#148](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/148)).
  Without that data, a request whose value reaches only plots, alerts, tables
  or logs lowers to `na` with a warning, and one that can reach a trade stops
  the run where its value is read
  ([#146](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/146)).
- A `request.security` symbol, timeframe or payload parameter that reaches the
  request through helper functions resolves on every call path, one context
  per value
  ([#146](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/146),
  [#148](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/148)).
  Payloads accept helpers with mixed tuples, multi-statement bodies and `var`
  state ([#144](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/144)),
  read `input.source` values and `hl2`-family history on the requested bars,
  register a computed lower timeframe with its first-bar value, keep epochs in
  64 bits ([#146](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/146)),
  and run helper `for` / `while` loops and a global's history on the requested
  bars ([#152](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/152)).

### Sessions

- `session.ismarket`, `session.ispremarket` and `session.ispostmarket` read the
  engine's session calendar and in-session facts, and `session.isfirstbar` /
  `islastbar` and their `_regular` forms read the engine's session-day members
  ([#142](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/142),
  [#145](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/145),
  [#150](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/150)).
  Inside a `request.security` payload they keep time-of-day predicates and
  warn. `session.<flag>[k]` reads the flag's history
  ([#144](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/144)).

### Values that came out silently wrong

- Pine's `int` is 64-bit: constant arithmetic such as `400 * 7200000` folds in
  64 bits, and an integer sum or product whose operands' known bounds can
  leave the 32-bit range (`days * 86400000`), or a product that `%` or `/`
  reads, is computed in 64 bits
  ([#149](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/149),
  [#151](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/151),
  [#152](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/152)).
- Inputs are read before `var` initializers
  ([#144](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/144)),
  and a `var` read with history initializes at its declaration
  ([#149](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/149)).
- `fixnan` and 17 other built-in argument slots evaluate their argument once, a
  function returns its last statement's value, and a script variable read
  with history inside a function reads that call site's history
  ([#146](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/146)).
  An `if` without `else` that runs no arm is `na`
  ([#148](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/148)).
- A deleted or collected drawing reads as `na`, and drawings are collected
  as TradingView collects them
  ([#146](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/146)).
- `color.new` / `color.rgb` bind a keyword transparency like a positional one
  ([#151](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/151)),
  and a fractional input or series transparency rounds to the nearest alpha
  byte as TradingView's does, where it was truncated
  ([#150](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/150)).
  A fractional constant `color.new` transparency, which TradingView
  truncates, now rounds too: a known divergence (10.5 reads back 11 where
  TradingView reads 10).
- A function whose untyped parameter receives different types gets one body
  per call site
  ([#151](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/151)).
- A binary operator evaluates its left operand first when the other operand
  can observe its effect
  ([#147](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/147)).

### Other translation changes

- A `ta.*` length fixed for the run builds the constant-length indicator on
  its first execution, and a series length of `ta.highest`, `ta.lowest`,
  `ta.highestbars` or `ta.lowestbars` re-windows every call, and
  `ta.supertrend` takes a series factor or ATR length and reads the values of
  its first execution, as TradingView does; a series length of any other
  `ta.*` stays refused
  ([#144](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/144)).
- A `switch` arm written on its `=>` line may be a comma list of statements,
  library overloads that differ by qualifier bind as TradingView binds them, a
  generic `input()` takes its default's type, and `syminfo.timezone` spells a
  UTC exchange zone `Etc/UTC`
  ([#152](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/152)).
- A script that declares `use_bar_magnifier = true` exports
  `strategy_declares_bar_magnifier()` to the host, and `syminfo.mincontract`
  reads the symbol fact the run declares
  ([#146](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/146)).

### Limits and generated names

- Untrusted source that would crash or hang the transpiler raises a located
  `CompileError`: source over 5 MiB (TradingView's 5MB compilation request),
  nesting over 512 levels, or a transpilation over 120 seconds (TradingView's
  compile limit). There is no statement-count limit. A 500-link `?:` chain, a
  500-branch `else if` ladder and a 2,000-element `array.from` transpile and
  compile; deeply nested calls no longer take exponential time.
  `transpile()` raises Python's recursion limit to 20,480 frames when it is
  lower. Numeric literals outside the C++ range raise a located error
  ([#140](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/140)).
- A Pine name that is a C++17/C++20 keyword, a standard macro or an engine
  name (for example `namespace`, `NULL` or `Box`) compiles: the generated C++
  spells it `pf_safe_<name>`, for variables, parameters, types, fields and
  tuple or loop bindings alike
  ([#140](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/140)).
  A script name that equals a host member the generated class reads is
  escaped too
  ([#144](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/144)).

### Packaging and documentation

- Update the direct build-tool `tar` dependency past the reported advisories
  and require a high-severity npm audit in release and publish gates
  ([#140](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/140)).
- Limit the Python source archive to package code and release documents so a
  VCS-free RC build cannot include installed Node dependencies or generated
  gate payloads
  ([#140](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/140)).
- One `VERSION` value gives every channel its spelling: a prerelease such as
  `1.0.0-rc.1` ships as PyPI `1.0.0rc1`, on npm dist-tag `next` and as a
  GitHub prerelease, never as `latest`
  ([#141](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/141)).
- Document the supported Python and gate/glue JSON contract, exact engine
  pairing, contribution checks, and release-note policy. Classify the package
  as Production/Stable for the 1.0 release
  ([#140](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/140)).

## 1.0.0-rc.1 — 2026-09-30

The release candidate for 1.0.0. Its changes since 0.10.4 are the ones listed
under 1.0.0; 1.0.0 changed only the version after it. It shipped as PyPI
`1.0.0rc1`, on npm dist-tag `next` and as a GitHub prerelease, and supports
only engine `v1.0.0-rc.1`.
