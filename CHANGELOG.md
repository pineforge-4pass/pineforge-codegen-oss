# Changelog

Release notes for `pineforge-codegen`. Versions of codegen and
[`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine) are
supported as exact pairs; see the [pairing rule](README.md#engine-pairing).

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

## 1.0.0 — Unreleased

This is the proposed 1.0 release note. The release lane sets the version and
date after the matching engine release is ready. Codegen `1.0.0` supports only
engine `v1.0.0`; `1.0.0-rc.1` supports only engine `v1.0.0-rc.1`.

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
  exactly, now warn.
- An inverted `array.fill` range leaves the array unchanged and an inverted
  `array.slice` raises TradingView's runtime error, as on TradingView; every
  `array.slice` warns that PineForge copies the slice where TradingView
  aliases it. `request.security` resolves a chart-symbol name by lexical
  scope, so a function-local rebind no longer blocks an unrelated global, and
  a `?:` symbol that can select another feed warns.

### Limits and generated names

- Untrusted source that would crash or hang the transpiler raises a located
  `CompileError`: source over 5 MiB (TradingView's 5MB compilation request),
  nesting over 512 levels, or a transpilation over 120 seconds (TradingView's
  compile limit). There is no statement-count limit. A 500-link `?:` chain, a
  500-branch `else if` ladder and a 2,000-element `array.from` transpile and
  compile; deeply nested calls no longer take exponential time.
  `transpile()` raises Python's recursion limit to 20,480 frames when it is
  lower. Numeric literals outside the C++ range raise a located error.
- A Pine name that is a C++17/C++20 keyword, a standard macro or an engine
  name (for example `namespace`, `NULL` or `Box`) compiles: the generated C++
  spells it `pf_safe_<name>`, for variables, parameters, types, fields and
  tuple or loop bindings alike.

### Packaging and documentation

- Update the direct build-tool `tar` dependency past the reported advisories
  and require a high-severity npm audit in release and publish gates.
- Limit the Python source archive to package code and release documents so a
  VCS-free RC build cannot include installed Node dependencies or generated
  gate payloads.
- Document the supported Python and gate/glue JSON contract, exact engine
  pairing, contribution checks, and release-note policy. Classify the package
  as Production/Stable for the 1.0 release.
