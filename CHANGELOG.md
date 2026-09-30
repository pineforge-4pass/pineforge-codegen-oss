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
