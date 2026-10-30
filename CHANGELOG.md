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

## Unreleased (1.9.1)

- Translation checks the generated C++ against a fixed allowlist and refuses a
  script whose output contains a construct outside it, with a fixed reason and
  no script text in the message (diagnostic codes `PF-E5053` through
  `PF-E5079`). The allowlist now also refuses namespace, mount and
  thread-creation names (`unshare`, `setns`, `clone`, `clone3`, `mount`,
  `umount`, `umount2`, `pivot_root`, `chroot`, `prctl`, `seccomp`,
  `pthread_create`, `thrd_create`, `__clone`, `thread`, `jthread`, `async`),
  and an authored identifier spelled like one of these is renamed with the
  usual escape prefix. A script that uses none of these names and no
  non-ASCII text translates to the same C++ as before, except for the timestamp
  change below.
- Every generated timestamp and time-field lambda that takes a timezone asks
  the engine whether the zone is accepted before it sets the process timezone;
  a rejected zone is read as UTC instead of being set. The generated C++ of
  every script that uses such a lambda changes by that one term, and the built
  strategy calls an engine library function (declared in the engine since 1.8.0),
  so it must be linked against the paired engine library.
- Identifiers, strings and comments in generated C++ are spelled in ASCII:
  non-ASCII identifier characters become encoded tokens and string bytes become
  octal escapes, with the same bytes at run time. A script with non-ASCII text
  generates different C++ text.
- The fallback renderer for a runtime technical-analysis length expression no
  longer rewrites text inside a string literal. It substituted identifier-shaped
  words everywhere in the expression, so a literal that spelled an input
  variable's name or a known string constant's name received that value's
  rendering and could close the literal early; it now substitutes identifier
  tokens only. The fallback is not reached by any script accepted so far, so no
  translated output changes.
- A string literal inside the length argument of a technical-analysis call is
  no longer rewritten when the call sits in a user function. The function's
  parameter substitution and local-variable expansion replaced identifier-shaped
  words everywhere in the argument text, so `ta.sma(close, mode == "fast" ? fast : 10)`
  inside `smooth(int fast)` called as `smooth(5)` compared `mode` with `"5"`
  instead of `"fast"` and chose the wrong length. Both now substitute outside
  string literals only, and so do the three earlier passes of the fallback
  renderer (`timeframe.` members, `math.` members and the words `and`, `or`,
  `not`), which changed the text of a literal such as `"buy and hold"`
  (the fallback renderer). A script
  without a string literal in such an argument generates the same C++ as before.
- A script that loops over every closed trade on every bar to sum profits
  under entry-id or profit conditions now generates a resumable fold: settled
  closed trades are folded once into prefix sums and only the unsettled tail is
  folded again each bar, in the plain loop's order, so every accumulated value
  is identical bit for bit. The per-bar work grows with the trades closed at
  the newest exit time and the trades closed since the previous bar, not with
  the number of closed trades. A script that does not match the loop shape
  generates the same C++ as before.

## Unreleased (1.9.0)

- Generated closed-trade text comparisons against a NUL-free string literal
  call the engine's in-place closed-trade comparison members, introduced in
  the engine release after `v1.8.0`. Generated C++ using these members builds
  only against that paired engine release. Every other shape keeps its owning
  string emission.

## 1.8.0 — 2026-10-29

Planned as 1.8.0, a minor release of the exact engine/codegen pair, paired with
engine `v1.8.0`. This entry records what changed in this repository since
1.7.0 and nothing else: the behaviour changes, the CHANGELOG-only change and
the fixes that ship in the same release. The behaviour changes alter the
published input manifest, the emitted C++ for scripts that read
`strategy.account_currency`, have a persistent declaration whose initializer
depends on earlier statements, have a helper-local `var color` or a
`request.security` rollback hook, use a pivot-levels helper or an array or
matrix history read, bind `fclose`, have a calendar timestamp lambda whose body
identifiers collide with requested-context bindings, or use
`calc_on_order_fills` with growing primitive arrays, maps or matrices, and the
accepted input (line endings, one-line field assignments, an unevaluated
`request.currency_rate`).

### Compatibility and migration

- Pairs with engine `v1.8.0` exactly. Regenerate strategy C++ with this release
  and relink every strategy library against the engine `v1.8.0` headers and
  `libpineforge.a`, even when the C ABI number is unchanged (README
  [Engine pairing](README.md#engine-pairing)).
- **Hosts must recognize the new input type `color`.** The input manifest
  (`transpile_full()` and the Pyodide/glue manifest) now reports every
  `input.color` entry as `"type": "color"` with an unsigned packed
  `0xAARRGGBB` integer `default` (`color.red` is `4294063685`, `0xFFF23645`;
  transparency is included), where 1.7.0 and earlier reported `"type": "string"`
  with a Pine-spelling default such as `"color.red"`. A host with a closed list
  of manifest types needs a `color` arm. Treat the default as a number in
  `0..4294967295`: do not parse it as a signed 32-bit integer, and send its
  decimal text to the native setter (`get_input_int64`). A default that cannot
  be folded to a constant keeps its previous (null) metadata. `supported`, the
  emitted C++, the native setters and receipts, and the C ABI are unchanged.
  `pineforge-hpo` accepts color inputs from its 1.8.0 release (a packed integer
  in `0..4294967295`, booleans refused).
- Every generated strategy library gains the two id-sites receipt exports (see
  the entry below); trading code is unchanged. Apart from that, the emitted C++
  is intended to change only for the scripts the entries below name: those that
  read `strategy.account_currency`, whose persistent declarations depend on
  earlier statements, with a helper-local `var color`, with a helper that reads
  the history of a derived argument, with a pivot-levels helper or an array or
  matrix history read, that bind `fclose`, with a calendar timestamp lambda
  whose body identifiers collide with requested-context bindings, or with a
  `request.security` (for the late-restore change alone, only the
  generated `restore_script_state()` changes, in 72 of the 555 corpus and
  fixture scripts; the other entries change the scripts they name). This is
  the design intent of the pull requests and their tests, not a byte
  comparison of generated C++ against 1.7.0. Nine diagnostic codes are added
  (`PF-E3093`, the refusal for a helper-local `var` of an unsupported type, and
  `PF-E3094` through `PF-E3101`, the refusals of a derived helper-argument
  history) and
  the user-facing text of `PF-E1088` and `PF-W1088` is reworded; the catalog
  schema is not changed.
- Engine behavior changes are described in the paired engine's own release
  notes; this entry does not summarize them.

### Changes since 1.7.0

- Preserve the calendar timestamp lambda's fixed body when a request
  expression also reads a mutable global, helper local, loop counter or tuple
  element whose name matches a body identifier, including history reads.
  Calendar argument expressions still read their requested-context bindings.
- Avoid libc name collisions in generated C++: pivot-level helpers use
  `open_price`, array and matrix history helpers use `open_slot`, and authored
  `fclose` names are escaped. Scripts using either helper change only that
  renamed identifier; scripts binding `fclose` change its escaped spelling.
- Avoid copying growing primitive arrays at each order-fill checkpoint when
  their writes can be tracked completely. Rollback records individual edits;
  bulk operations retain full preimages, and other arrays keep their existing
  snapshots. The generated strategy class of a script that uses this is not
  copyable or movable.
- Avoid checkpoint copies for isolated primitive maps and matrices with fully
  tracked writes. Sparse writes use individual undo records; bulk mutations
  save at most one full preimage per root between checkpoint or restore
  boundaries. Dense writes switch to that preimage after exhausting the
  element-count budget, preserving key order, matrix shape and handle identity.
- Reuse unchanged primitive-array history snapshots when every write can be
  tracked, while preserving detached historical values and order-fill rollback.
  The generated strategy class of a script that uses this is not copyable or
  movable.
- Fix a silent wrong value under `calc_on_order_fills`: a fill recalculation
  that ran after a deferred `request.security` evaluation (for example a late
  margin-call liquidation) restored the strategy checkpoint taken before that
  evaluation and rolled the requested context's own state back (helper-local
  `var` series, requested TA objects, OHLC and expression histories, mutable
  copies and bar-index clocks), while the engine's requested aggregators had
  already advanced. The generated `restore_script_state()` now leaves that
  requested progression alone and still restores the chart's state and the
  merged request results; payloads that keep user calls on the chart's bar
  (the chart-fallback warning) keep their old rollback, and so do the shared
  helper-local `var` series of a script that has such a payload, so the
  late-restore loss is fixed for every script without that warning and not
  for the helper-local state of a script that has it. The emitted C++ changes
  only in that one function; no engine change or ABI change is involved.
- Initialize a persistent (`var`) UDT, array, map or matrix once at its
  declaration, in statement order, when its initializer reads a value computed
  earlier in the statement walk: an earlier declaration, reassignment, field
  write, collection method or global read by a user helper
  ([#226](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/226)).
  The first-bar preamble previously read the zero default. Emitted C++ changes
  only for scripts with such a dependency; independent constructors and
  callable-local declarations keep their routes. A `varip` declaration with the
  same dependency takes the same route, with its storage and once flag left out
  of the `calc_on_order_fills` rollback checkpoint
  ([#258](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/258)).
- Raise currency-conversion errors only when `request.currency_rate` is
  evaluated, not when the call merely appears in a branch that never runs
  ([#228](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/228)).
  No currency-rate data feed is added: an evaluated call stops the run with a
  no-data error. Only the diagnostic text of scripts that previously failed to
  translate changes, and the wording of one warning; a call that names its second argument `to = ...` still
  does not parse, as before.
- Support explicit and inferred helper-local `var color` state in
  `request.security`, preserving persistence, transparency and `na`
  ([#229](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/229)).
  The epoch-width integer and the collection, object and drawing refusals are
  unchanged; the refusal for any other unsupported helper-local `var` type has
  the new code `PF-E3093`.
- Sample the history of a derived helper argument on the requested context's
  own bars in `request.security`: a helper such as `f(x) => x[1]` called as
  `f(close - open)` now reads the previous value of the argument on the
  requested bars, not the chart's bars
  ([#229](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/229)).
  The argument is sampled through one ring per call site, which the
  late-restore rule above leaves alone with the rest of the requested state.
  Shapes that cannot be sampled are refused with located errors, `PF-E3094`
  through `PF-E3101`: a derived history offset that is not a non-negative
  constant integer, a ring capacity that is not representable, a call that is
  not unconditional, a helper that depends on other helpers' state, history
  composed with helper-result history, a mutable global that cannot be
  replayed, and a parameter whose type or width has no lossless history.
  Direct OHLC and time bindings, builtin forms and method envelopes keep their
  existing lowering.
- Accept a UDT field assignment (`:=`, `+=`, ...) as the body of a one-line
  function or method, `set(State s) => s.value := 1`, translating like the same
  body on its own indented line
  ([#230](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/230)).
  A plain-name or subscript target in a one-line body keeps its parser error.
- Fix `input.color` metadata so its default round-trips through the native
  input setter ([#231](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/231),
  [#251](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/251)).
  Python `transpile_full()` and the Pyodide/glue manifest now report
  `type: "color"` and an unsigned packed `0xAARRGGBB` integer default for named
  Pine v6 colors, hex literals and literal `color.new` / `color.rgb`
  expressions, including transparency. Unfoldable defaults retain their previous
  metadata; accepted expressions are unchanged. See the migration note above.
- Fix `strategy.account_currency` to return the declared constant currency,
  falling back to the symbol currency when omitted or `currency.NONE`, and
  support currency-constant comparisons and string concatenation
  ([#227](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/227),
  [#250](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/250)).
  No currency conversion is added; only the value the script reads changes, so a
  script that branches on `strategy.account_currency` can now take a different
  branch than under 1.7.0.
- Accept CRLF and lone CR code line endings like LF across the Python and JSON
  translation entry points, preserving generated C++, diagnostics and literal
  string contents
  ([#230](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/230),
  [#252](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/252)).
  This is the line-ending part of #230; a lone `\r` inside a string literal
  stays data. Output for LF sources is unchanged.
- Report how many order sites create a computed id in one execution of the
  script (not per bar: `calc_on_order_fills` runs the script again after each
  fill). Every generated
  strategy library also defines `strategy_id_sites_api_version()`, which returns
  1, and `strategy_id_sites_receipt(s, json, capacity, required, error,
  error_capacity)`, beside the order-shapes receipt and with the same buffer
  protocol. Its canonical JSON, `{"strategy":{"ids":{"freshPerBar":K}},"version":1}`,
  counts the lowered `strategy.entry`, `strategy.order` and `strategy.exit` sites
  whose `id` is not a literal, once per static call of the function that holds
  them and once per iteration of a `for` loop with known bounds; a loop without a
  static bound or a recursion around a computed id gives
  `{"strategy":{"ids":{"freshPerBarStatus":"unbounded"}},"version":1}`. The
  emitted C++ of every strategy gains the two exports; trading code is unchanged.
- Backfill release notes for earlier codegen releases, including the Security
  entry for GHSA-5www-2x9c-mgr6
  ([#248](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/248)).

## 1.7.0 — 2026-10-25

A minor release of the exact engine/codegen pair, paired with engine `v1.7.0`
and dated 2026-10-25. This entry records what changed in this repository since
1.6.3, and nothing else: three pull requests merged after `v1.6.3`, one of which
changes the emitted C++ and the other two only the repository's own CI.

### Compatibility and migration

- Pairs with engine `v1.7.0` exactly. Regenerate strategy C++ with this release
  and relink every strategy library against the engine `v1.7.0` headers and
  `libpineforge.a`; this applies even when the C ABI number is unchanged
  (README [Engine pairing](README.md#engine-pairing)). Replacing the archive
  alone does not update a statically linked strategy library.
- The emitted C++ changes, as described below: every generated library gains a
  new exported function, the generated constructor gains a guarded statement
  for scripts that do not read closed-trade rows, and the set of reserved names
  grows. The Python API, the diagnostic codes and severities, and the catalog
  schema are not changed; the order-shapes receipt's `host_reads` list gains
  `set_trade_history_streamable` for scripts that do not read closed-trade rows.
- Engine behavior changes are described in the paired engine's own release
  notes; this entry does not summarize them.

### Changes since 1.6.3

- Declare whether the emitted code reads closed-trade rows, and let scripts that
  do not read them opt into streamed trade history
  ([#213](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/213)).
  Generated libraries now export `int strategy_reads_trade_history(void)`: `1`
  when the emitted code reads a `strategy.closedtrades.*` accessor, `0` when it
  does not. The closed-trade count (`strategy.closedtrades` alone) and the
  open-trade accessors (`strategy.opentrades.*`) do not count as reads. For a
  script that does not read closed-trade rows, the generated constructor calls
  `this->set_trade_history_streamable(true)`, guarded by
  `#if defined(PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1)` so that it compiles
  against engine headers that lack the setter; a script that reads closed-trade
  rows emits no such call, and the order-shapes receipt's `host_reads` names the
  setter for non-readers only. The constructor's host calls are now qualified
  (`this->configure_pine_strategy(cfg)`). A library generated by an earlier
  release has no such export, and its absence does not establish that the
  script is a non-reader. What the engine does with the streaming setting is
  described in the engine `v1.7.0` release notes.
- Reserve more names in generated C++ and escape them as `pf_safe_<name>` when a
  Pine identifier uses one (same pull request, documented in the new
  "Identifiers in generated C++" section of
  [docs/PUBLIC_CONTRACT.md](docs/PUBLIC_CONTRACT.md)): the engine namespaces and
  types that `using namespace pineforge;` brings into scope (`report`,
  `report_sink`, `report_sequence`, `source`, `native_calendar`, `pine_color`,
  `RunFailureCode`, `RunFailureInfo`, `QtyType`, `CommissionType`, `XLoc`,
  `YLoc`, `normalize_timezone_for_posix`), the generated C exports
  (`strategy_create`, `strategy_create_checked`, `run_backtest`,
  `run_backtest_full`, `run_backtest_full_checked`, `strategy_free`,
  `report_free`, `strategy_set_input`, `strategy_set_input_checked`,
  `strategy_set_override`, `strategy_set_override_checked`,
  `strategy_set_magnifier_volume_weighted` and the new
  `strategy_reads_trade_history`), the feature macros
  `PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1` and
  `PINEFORGE_HAS_SYMBOL_SECURITY_EVAL_V1`, and the host member
  `set_trade_history_streamable`. A Pine `type report`, for example, now becomes
  `struct pf_safe_report`; before, such a script could fail to compile against the
  1.7.0 engine headers with an ambiguous reference. User-defined type handles
  and `request.security` evaluators use the escaped spelling too. An identifier
  that does not collide keeps its spelling. The existing `CPP_RESERVED` export lists the new
  names; no new reserved-name API is introduced.
- Repository and CI only, no effect on emitted C++ or on the Python and JSON
  contract: the `promote-baseline` workflow refuses untrusted heads and actors
  (SEC846,
  [#242](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/242)),
  and `test.yml` adopts the organization's CI canon (CR-241,
  [#243](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/243)).

## 1.6.3 — 2026-10-23

A patch release of the exact engine/codegen pair, paired with engine `v1.6.3`
and dated 2026-10-23. This entry records what changed in this repository since
1.6.2 and nothing else: one security fix to the emitted C++, merged from a
private fork (commit `041889818e2206fdf7261574128064fb0b4142ad`), and four pull
requests that change only the repository's own CI, publishing and documentation.

### Security

- Published advisory
  [GHSA-5www-2x9c-mgr6](https://github.com/pineforge-4pass/pineforge-codegen-oss/security/advisories/GHSA-5www-2x9c-mgr6),
  "Codegen can emit C++ calls and type names taken from the input script"
  (severity high, CWE-94). Fixed in 1.6.3 of both packages. The affected
  versions are `pineforge-codegen` from 0.6.4 up to but not including 1.6.3 and
  `@pineforge/codegen-pyodide` from 0.7.0 up to but not including 1.6.3, which
  ships the same Python sources. Images of
  `ghcr.io/pineforge-4pass/pineforge-release` before 1.6.3 contain an affected
  codegen; the 1.6.3 image contains the fixed one. Upgrade to 1.6.3 or later; the
  advisory states the mitigation and the upgrade steps and, like this entry,
  contains no reproduction recipe.

### Compatibility and migration

- Scripts that reached the two affected paths are now refused instead of
  transpiled. A call or member that the codegen has no lowering for ends with
  `<name>(...) is not implemented in PineForge runtime.` where it used to be
  written into the emitted C++; a token that is not a type, in a type position
  or inside `<...>`, ends with a located parse error; and `array.new` or
  `array.from` with an undeclared element type is refused with the same
  "not implemented" message. A script that compiled under 1.6.2 can therefore
  fail with a compile or parse error under 1.6.3, and this is the intended
  effect of the fix. For scripts that stay accepted the emitted C++ is unchanged
  except that the default of an enum-typed element is `na<double>()` (a type
  name that is not a declared user type falls back to its slot's default) and
  `format`, `currency` and `order` values are typed `std::string`. A pair change
  requires regenerated C++ and relinked strategy libraries against the paired
  engine release's headers and `libpineforge.a` (README
  [Engine pairing](README.md#engine-pairing)); regenerate any C++ that an
  affected release produced from a script you do not trust.

### Changes since 1.6.2

- Run every codegen job on GitHub-hosted runners
  ([#237](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/237)).
- Publish to npm only by operator dispatch, in the main-only `npm-publish`
  environment
  ([#238](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/238)),
  and state the npm publishing bound as it now exists: both npm jobs run in that
  environment
  ([#240](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/240)).
- Stop naming a retired private campaign repository in five lines of the
  documentation
  ([#239](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/239)).

## 1.6.2 — 2026-10-21

A patch release of the exact engine/codegen pair, paired with engine `v1.6.2`
and dated 2026-10-21; the prerelease `1.6.2-rc.1` (2026-10-19) pairs with engine
`v1.6.2-rc.1`. This entry records what changed in this repository since 1.6.1
and nothing else: a checker for generated C++ and a reserved-name change in the
emitter, a change of the licensor's written entity, and CI changes.

### Compatibility and migration

- New command `pf-cpp-allowlist` (`pineforge_codegen.cpp_allowlist:main`,
  installed with the package), a checker for a generated C++ translation unit
  that flags dangerous primitives
  ([#210](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/210),
  CR-164 S1). It is a denylist with known false positives, a mitigation and not
  proof that a unit is safe (the wording of the 1.6.3 security advisory).
- A script identifier equal to a symbol that the checker refuses when it appears
  bare (`system`, `free`, `socket`, `open` and the rest of the list in
  `pineforge_codegen/codegen/helpers.py`, `LIBC_RESERVED_NAMES`) is now emitted
  as `pf_safe_<name>` instead of the bare libc name, so the emitted C++ for
  scripts that declare such a name changes.
- A pair change requires regenerated C++ and relinked strategy libraries against
  the paired engine release's headers and `libpineforge.a` (README
  [Engine pairing](README.md#engine-pairing)).

### Changes since 1.6.1

- The licensor entity is written exactly as `PINEFORGE LLC` in `LICENSE`,
  `LEGAL.md` and `CONTRIBUTING.md`
  ([#234](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/234)).
- CI: Linux x64 jobs on the pf-ci-spot pool
  ([#212](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/212),
  tracked at fragment b240f6d7 in
  [#215](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/215)),
  credentialed release and deploy jobs kept on hosted runners
  ([#214](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/214)), a
  guarded job and a token-free OIDC job to claim the PyPI `tunaxis-codegen` and
  npm `@tunaxis/codegen-pyodide` names with a 0.0.1 placeholder
  ([#216](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/216),
  [#217](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/217)), the
  App-token owner following the repository owner
  ([#221](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/221)),
  the open Dependabot bumps folded and pinned by SHA with the exact release
  ([#223](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/223)),
  superseded pull-request runs of the gate and test workflows cancelled
  ([#225](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/225)),
  `timeout-minutes` dropped from `gate.yml` and `test.yml`
  ([#233](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/233)),
  and the pull-request suite skipped on draft pull requests
  ([#236](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/236)).

## 1.6.1 — 2026-10-18

A patch release of the exact engine/codegen pair, paired with engine `v1.6.1`
and dated 2026-10-18; the prerelease `1.6.1-rc.1` pairs with engine
`v1.6.1-rc.1`. This entry records what changed in this repository since 1.6.0,
and nothing else: after
[#209](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/209) added
the 1.6.1 changelog entry for `v1.6.1-rc.1`, only the final release-note and
pairing documentation updates landed before `1.6.1`.

### Compatibility and migration

- No Python, JSON, diagnostic or C ABI contract changes.
- Strategies that previously failed native C++ compilation when calling a
  calendar built-in (`hour`, `minute`, `second`, `dayofmonth`, `dayofweek`,
  `month`, `year`, `weekofyear`) with `timezone=` and no positional `time`
  argument, or when initializing a `var` array with `array.from(...)` over
  unary-signed or mixed numeric constants, now emit matching C++ types and
  compile cleanly.
- A pair change requires regenerated C++ and relinked strategy libraries against
  the paired engine release's headers and `libpineforge.a` (README
  [Engine pairing](README.md#engine-pairing)).

### Changes since 1.6.0

- Bind the omitted `time` argument of calendar built-ins (`hour`, `minute`,
  `second`, `dayofmonth`, `dayofweek`, `month`, `year`, `weekofyear`) to the
  active bar's timestamp (`current_bar_.timestamp` on the chart, `bar.timestamp`
  inside `request.security`) when called with a `timezone=` keyword argument
  only
  ([#205](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/205)).
- Infer a single consistent element type across every `array.from(...)` call
  (`var` array declarations, plain declarations and inline arguments alike) in
  `pineforge_codegen/codegen/types.py` and `pineforge_codegen/analyzer/types.py`,
  preserving `int` for integer constants (including unary-signed constants and
  function-local constants) and promoting to `double` when any element is
  `float`. For previously compiling scripts, the observable emitted-C++ change
  is that an `array.from` whose arguments are all integer, including
  unary-signed ones like `array.from(-1, 1)`, now emits `std::vector<int>`
  instead of `std::vector<double>`; with `na` mixed in (`array.from(-1, na)`) it
  emits `int` with the `int` `na` sentinel, as `array.from(1, na)` already did.
  Values are unchanged for arithmetic, since `/`, `%`, `math.max` and the array
  reductions already work in `double`
  ([#205](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/205)).
- Group weekly Dependabot pull requests into one pull request per ecosystem
  (`npm` and `pip`) in `.github/dependabot.yml`
  ([#206](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/206)).

## 1.6.0 — 2026-10-17

A minor release of the exact engine/codegen pair, paired with engine `v1.6.0`
and dated 2026-10-17; the prerelease `1.6.0-rc.1` pairs with engine
`v1.6.0-rc.1`. This entry records what changed in this repository since 1.5.0,
and nothing else: after
[#203](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/203) added
the 1.6.0 changelog entry for `v1.6.0-rc.1`, only the final release-note and
pairing documentation updates landed before `1.6.0`.

### Compatibility and migration

- The analyzer and the code generator do not change behavior: the only edits
  under `pineforge_codegen/` since 1.5.0 are comments and docstrings in five
  files (#202), so the C++ the transpiler emits for a script and the Python
  and JSON contract are unchanged. No diagnostic code, severity or catalog
  schema changes.
- The [release note policy](#release-note-policy) still applies: "A pair
  change requires regenerated C++ and relinked strategy libraries, even
  when the C ABI number is unchanged." Regenerate and relink against the
  new engine's headers and static library.
- Engine behavior changes, including failure codes and the report contract,
  are described in the paired engine's own release notes; this entry does
  not summarize them.

### Changes since 1.5.0

- Remove the 11 gate-corpus pines that copy probes leaving the engine corpus
  ([#200](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/200)).
- Remove the subprocess time limits of CI tests and checks
  ([#201](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/201)).
- Move the recorded US-listed, futures, forex and metal tapes of the tests to
  the private fixtures, and reword the comments and docstrings that cited them
  ([#202](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/202)).

## 1.5.0 — 2026-10-12

A minor release of the exact engine/codegen pair, paired with engine `v1.5.0`
and dated 2026-10-12. This entry records the diagnostic additions below and the
report-contract documentation for the paired engine fix, and nothing else: the
codegen change is diagnostics only; the report documentation adds no Python,
JSON, diagnostic or emitted-C++ change of its own, and no analyzer or
code-generation file differs.

### Compatibility and migration

- **`note` is a third diagnostic severity, and it is nonfatal.** A diagnostic
  is now an `error`, a `warning` or a `note`: `Level.NOTE` in Python and `"note"`
  as the `severity` of a catalog entry and of a JSON diagnostic. A note is
  nonfatal like a warning. `transpile_full()["diagnostics"]` and the success
  envelope's `diagnostics` list warnings and notes, a script with only notes
  still returns `ok: true` and its C++, and a `CompileError` can carry notes
  beside its errors. `transpile()` still returns no nonfatal diagnostics.
  `CompileError.format()` prints `note[...]` where it printed `warning[...]` for
  the codes below. Code written for two severities must handle `note`: read an
  unknown severity as a warning, never as an error. A note does not mean that
  results are unaffected; PF-W1505 describes a warmup approximation.
- **Exactly eleven codes change from warning to note:** PF-W1505, PF-W1508,
  PF-W1509, PF-W1525, PF-W1526, PF-W1527, PF-W1528, PF-W1529, PF-W1530,
  PF-W1531 and PF-W1532. They describe calls PineForge skips as visual only
  (`plot(...)`, `table.new(...)`), earnings, dividends, splits, financial,
  footprint and other-symbol requests whose value reaches only display and alert
  sinks and is lowered to `na`, and the `ta.ema` warmup approximation. Every
  other code keeps its severity. PF-W1502 (`color(...)` cast), PF-W1506
  (`color.from_gradient`), PF-W1507 (drawing setters that drop color, style or
  size), PF-W1078 (visual or style constants) and PF-W1552 (a
  `request.security_lower_tf` whose value reaches only display and alert sinks)
  also speak of visual-only or display-only handling and remain warnings: a
  message's wording does not classify it. A consumer that tallied these eleven
  codes as warnings, to gate a run, count warnings or color a banner, now
  receives notes. PF-W1509 still classifies as PF-W1508 where the two templates
  tie, so no new emitted code appears.
- **`user_message` is a raw ICU template over `args`.** Every catalog entry,
  every `Diagnostic` (`Diagnostic.user_message`) and every JSON diagnostic gains
  `user_message`: a short English ICU template that uses only that diagnostic's
  existing `args` names and may be constant (`{name}() does nothing in a backtest.`
  with `args.name = "plot"`). It is not pre-rendered English. Render it with
  `args` as plain text, never as template syntax or markup, or translate by
  `code`; do not display the unrendered template. It does not replace `message`
  and `hint`. The templates of the eleven migrated codes are fixed string for
  string by a delta fixture. Every template is checked for form (short,
  parseable, over that code's own `args` names). Each of the 97 warning twins
  of an error (`PF-W1nnn` for `PF-E1nnn`) has a sentence of its own that says
  PineForge warns, and the 91 that warn only inside a `switch` arm also say it
  keeps the arm's lowering. No warning or note template says that PineForge
  refuses or rejects something, though one may say what TradingView refuses or
  rejects. Beyond these checks a template's exact wording is not pinned.
- **Legacy fields are stable.** `code`, `args`, `message`, `hint`, `line`,
  `col`, `endCol`, `ok`, `error` and the envelope keys keep their meaning and
  values: no key is renamed or removed, and no code is renumbered or reused (the
  notes keep their `PF-W` codes, and the prefix is not the severity).
  `render_diagnostic()` still returns the unchanged English `message` and
  `hint`. The catalog schema id stays `pineforge-diagnostics-catalog/v1`, so a
  reader that checks `severity` against two values or rejects unknown entry
  fields must accept `note` and `user_message`. The receiver examples in
  `tests/fixtures/diagnostic_notes/receiver.json` specify the compatibility
  rule; they are not evidence of any application's behavior.
- **The warmup sidecar is later work.** No compile-time or runtime sidecar
  exists in this entry. PF-W1505 stays the static `ta.ema` note it was, with
  only its severity changed: the support checker emits it at each `ta.ema` call
  it visits, with no per-call condition or suppression and no call-site
  identifier. The startup inventory and extraction that would bind a versioned
  sidecar to a diagnostic by `call_site_id` and source range, so that a consumer
  can show a startup sentence only for a known finite shortfall, are not part of
  this entry, and no diagnostic carries a call-site identifier or a startup
  length.

### Documentation

The engine side of the report-ownership text below is a report fix, in
preparation, that engine `v1.4.0` does not carry.

- The [public contract](docs/PUBLIC_CONTRACT.md#a-report-belongs-to-the-call-that-returned-it)
  states that a report belongs to the call that returned it. A call the engine
  refuses without beginning gets the empty report, never an earlier run's rows;
  a run that began and then failed keeps its partial rows; the explicit stream
  snapshot keeps the owner's rows; recorded outputs keep their own record of
  the last run that began.
- Read the error text, the error code **and** the run status after every run.
  A refusal the engine makes before it admits the begin leaves the status at its
  earlier value (0 after a completed run), so the status alone misses it. The
  README's "Compile & run against the engine" section says the same.
- Pairing is unchanged in kind: regenerate the strategy C++ and relink against
  the engine release's headers and `libpineforge.a` on a pair change (README
  [Engine pairing](README.md#engine-pairing)).

## 1.4.0 — 2026-10-08

A minor release of the exact engine/codegen pair. Its behavior was validated
before tagging on codegen `bfc4ddce` and engine `b3192bfc`.
It consolidates the seven requests landed
since `v1.3.0`: [#183](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/183)
(emission ordering), [#184](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/184)
(coded run stops), [#185](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/185)
(input manifests), [#186](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/186)
(collection history and binding), [#188](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/188)
(CI history), [#189](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/189)
(reference tests), and [#190](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/190)
(emitted-string regressions and edge cases). Recorded-output support from
engine request #356 is excluded. The pre-tag validation used these commits
with `VERSION` equal to `1.3.0`; those results are not post-tag measurements.

### Compatibility and migration

- Codegen 1.4.0 supports only engine `v1.4.0`. Regenerate every strategy's C++
  and relink with that release's generated headers and `libpineforge.a`.
  The pair retains C ABI version 4, settings/capability API version 1 and the
  `engine_script_run_v19` epoch; equal version numbers for those interfaces
  do not make mixed engine/codegen releases supported.
  A report's fingerprint records engine/codegen versions, generated C++'s
  hash and the paired engine's corrected provenance values. The retained
  epoch and hash domain tags do not promise equal fingerprints, state-hash
  values or trades across 1.3.0 and 1.4.0.
- Emitted C++ changes, including common run-stop shims and collection history
  wrappers. Do not reuse a 1.3.0 strategy library or assume source identity.
  The public Python call signatures and override keys stay the same, but
  `inputs` values and the Python/Pyodide success envelopes change as described
  below. These are not blanket byte-identity promises for emission or envelopes.
- Every manifest row now carries `supported`. When it is `false`, hide the
  input, exclude it from sweeps and optimization, and do not send a value:
  the strategy uses its compiled default, and the paired engine refuses a
  supplied value with `setting_unsupported`. `supported: true` alone does
  not validate the value's type, bounds or options, or make a shared key safe.
  A supported empty dropdown and an unsupported string input can both have
  `options: []`; that list does not determine support.
- Give each input a unique title before upgrading. Calls with the same title
  or fallback declaration key still produce separate manifest rows with the
  same `title`; their `supported` flags do not detect the collision. Different
  `group=` labels do not separate keys. On the paired 1.4.0 engine's normal
  checked path, a completed run of such a script has `fingerprint: null` even
  without an override. Supplying the shared key fails before execution with
  `setting_rejected`, reason `ambiguous_key`. For example,
  `Period="7"` returned a success report on 1.3.0, whose legacy setter applied
  a shared key to all matching inputs. Legacy-path unresolved provenance rows
  may still appear inside a fingerprint; that differs from the whole
  fingerprint being null on the checked path.
- The paired engine's run harness now uses the checked settings API. Stored
  presets and sweep values must satisfy the compiled input's type, bounds and
  options. Two other examples that succeeded on 1.3.0 now fail with
  `setting_rejected`: `Color="color.blue"` (`expected_integer`) and
  `Choice="15"` for options `[10, 20, 30]` (`invalid_input_option`). A color's
  manifest still has `type: "string"` and a Pine spelling such as `"color.red"`
  as its default, but the checked native input requires the packed integer;
  do not forward that manifest string as a native color value. The checked
  API also validates parseable numbers, booleans, enum members and known keys.
  This does not mean all invalid settings previously succeeded:
  `initial_capital="abc"` and `initial_capital="-1"` already failed on 1.3.0;
  they now report `expected_finite_decimal` and `value_below_minimum` reasons.
  Legacy C setters retain their existing behavior.
- The paired engine adds `strategy_get_last_error_code` and
  `strategy_get_last_error_args` beside the existing English error getter,
  under `PINEFORGE_HAS_RUN_FAILURE_CODES_V1`. Its run harness exposes `code`
  and typed `args` beside `error` on failures. Check the failure channel even
  when the English message is empty; do not accept a stopped run's partial
  output as success or parse the English to recover a code. These run failures
  are distinct from transpile-time `CompileError` diagnostics.
  `setting_rejected` is catalog class `input`; `setting_unsupported` is class
  `unsupported`. A deliberate `runtime.error` is `strategy_runtime_error`,
  class `strategy`. The installed engine entrypoint returns exit 4 for all
  three codes: exit 4 alone does not select a billing or refund decision.
- The paired engine certifies scalar input/override types in fingerprint
  provenance; unresolved values remain explicit rather than being treated as
  certified native values. This is an engine report change, not another field
  added to the transpile-time input manifest. See the paired engine's release
  notes for report resolution reasons and counts.

### Emission and run failures

- Requested-context history resets and TA variant position ties no longer
  use address or set order; they preserve traversal order. Refusal
  diagnostics for conflicting scalar history and map locals are hash-seed
  independent.
- Generated run stops use stable engine failure codes when the paired engine
  advertises `PINEFORGE_HAS_RUN_FAILURE_CODES_V1`. No-data and other-symbol
  stops carry compile-time request provenance; copying their English into
  `runtime.error` cannot select their codes. Older engines keep the legacy
  throws and English messages.
- `runtime.error(message = "...")` now passes the named message. With the
  coded engine and run harness, a run stopped by `runtime.error()` with an
  empty or named message no longer reports success or exposes partial results.
- `str.tonumber` no longer hides a stop raised while evaluating its argument;
  only numeric parse failures become `na`. Invalid array sizes, substring
  positions and overflowing format-placeholder indices have checked stops.
  String parsing and checked substring calls do not capture authored helper-like
  names; substring arguments retain source order without copying the source text.
- Generated run wrappers preserve the inner failure code, and latched setter
  failures and checked strategy construction retain their failure identity.

### Python and Pyodide input metadata

- The transpile-time input manifest (`transpile_full()["inputs"]` and the JSON
  envelope of the Pyodide package) gains `supported`, the checked-settings
  receipt's flag, on every input, and reads its defaults, choices and bounds
  from the descriptor that generates the receipt, with the retained limitations
  below. No manifest key is renamed or removed, but these values of existing
  keys change:
  - Source inputs (`input.source`, a plain `input(close)`): `default` is the
    series name, where it was `null`, and `options` lists the nine native
    sources, where it was absent.
  - Enum inputs: `options` lists the `Enum.member` choices, where it was
    absent; `default` keeps its `Enum.member` spelling. The compiled receipt
    encodes that default as its integer member value.
  - String inputs: a built-in constant publishes its runtime value
    (`alert.freq_all` is `"all"`, `currency.USD` is `"USD"`; the manifest held
    the Pine names), a named string constant its value, and the choices of
    `input.timeframe` and `input.session` are listed. What the receipt cannot
    represent (`size.small`, `position.top_right`, `na`) has `supported: false`
    and `options: []`; an unavailable default is `""`, but a representable
    default survives an unsupported choice. String decoding uses the emitter
    escape grammar, keeps literal Unicode and escaped backslashes, and stops
    at the first NUL like the receipt's `std::string(const char*)` constructor.
    NUL is emitted as a fixed three-digit octal escape even before digits;
    this is consistency with current native truncation, not lossless NUL storage.
  - Typed `int`, `float`, `price` and `time` inputs: a signed or
    named-constant `default` is a number, where it was `null`; `min`, `max` and
    `step` appear for signed and named-constant bounds (`minval=-80` was left
    out); an `options=[...]` dropdown lists its numbers. A literal or inlined
    `bool` default publishes a boolean, not a number.
  - A plain `input(-5)` or `input(-2.5)` is typed `int` or `float` with its
    number as `default`, where it was `string` with `null`. The compiled
    getter and checked receipt keep their existing float type for these
    signed plain inputs; this manifest correction does not change that lowering.

  Values the receipt computes at run time (`LEN * 2`, `not true`,
  `timestamp(year, month, ...)`) stay unpublished: the default remains
  `None`/JSON `null`, an unfoldable bound is omitted, and the whole `options`
  field is omitted when any numeric choice is unfoldable. An `input.color`
  retains its Pine spelling as a string default, not the receipt's packed
  integer; other nonliteral plain `input()` calls remain `string` with no
  published default (the source case above is the exception). A folded
  `timestamp("...")` is a publishable literal. These gaps
  are explicit in `tests/test_input_metadata_receipt.py::KNOWN_LIMITS`, not a
  claim of arbitrary expression evaluation or language soundness.
- Documents the timeframe spellings of two surfaces, both stable: a capability
  receipt records a request's timeframe as the script wrote it (`"D"`), and
  request discovery and feed keys use the engine's spelling (`"1D"`).

### Collection lowering and refusals

- Bind keyword arguments of the matrix methods and the checked array methods to their own parameter slot, with the omitted optional parameters taking their defaults in place (`matrix.sort(m, order = order.descending)`, `m.submatrix(to_column = 1)`, `a.fill(7.0, index_to = 1)` no longer drop or shift a keyword), and refuse a keyword that names no parameter, or a parameter given twice, with the existing argument diagnostics. A defaulted `submatrix` evaluates its receiver once, an index-only `add_row` / `add_col` takes any numeric index, and a bound `array.concat` result (`c = a.concat(b)`, `c := array.concat(a, b)`) keeps the unsupported-function refusal. An array history that reaches a user function's or method's parameter keeps its earlier copy lowering and the earlier refusal of a callee that checks it with `na()`; a bound missing-history matrix reports the recorded na-ID runtime error.
- Compile matrix/matrix and matrix/scalar `matrix.sum` overloads with matrix-valued results, including helpers and history. Support scalar `matrix.diff`/`matrix.mult` and omitted optional arguments to matrix row/column insertion, submatrix, sort and array fill; unsupported matrix/vector multiplication and bound `array.concat` results report an existing unsupported-function diagnostic instead of failing C++ compilation.
- Preserve na array IDs when a history offset has no value, including aliases and `na()`-only bindings; array methods on those IDs report the existing runtime error instead of crashing. Bounds-check legacy collection-element reads and writes to prevent unchecked access.

The inherited follow-ups remain: keywords on UDT matrix fields, `matrix.new`
and array methods outside the checked table; `matrix.fill` range handling
(both spellings still fill the whole matrix); matrix diff/mult dimension
checks; and nullable history across callable/iteration shapes not covered by
the documented lowering. This release does not claim those cases resolved.

### Emitted-string edge cases and maintenance

- Return the prefix or ticker of the supplied symbol in `syminfo.prefix(symbol)`
  and `syminfo.ticker(symbol)` instead of always reading the chart symbol; the
  variable forms, no-argument calls and direct chart-ID calls keep their
  chart lowering. Supplied-symbol calls split at the first colon; a prefix
  is empty without a colon or with no ticker after it, and a ticker without
  a colon is the full string. Non-string arguments still compile and yield
  an empty string, including the retained string-`na` typing gap.
- Refuse malformed library versions with the existing import diagnostic instead
  of allowing them to raise outside the JSON gate, including non-ASCII decimal
  digits and ASCII versions exceeding Python's integer-conversion limit.
- Reserve generated receipt exports and version macros against script names.
- Escape embedded NUL values in generated C++ string literals, and strengthen
  emitted-string regression tests with explicit emitter reach and round trips.
- CI fetches full branch history for historical reference tests (#188); two
  whole-C++ comparisons use the accepted immutable scaffold (#189). These
  changes affect verification only, not the compiler or execution semantics.

The hardening wording remains scoped to the previously documented emitted-string
validation and escaping; these edge-case repairs and regression checks are not
a new arbitrary-source security guarantee. No exploit payloads are included.

### Known issues with consumers

The new numeric options expose an exact-type comparison in
`pineforge-hpo` 0.11.0: a JSON integer `20` is refused with
`invalid_input_option` for an `input.float` choice `20.0`, although the
numbers are equal. For HPO 0.11.0, send each selected value with the number
type the original manifest publishes for that option: `20.0` when it lists
`20.0` (explicit JSON `20.0` or a Python float), and integer `20` when it lists
`20`. A float input written with whole-number-literal options such as
`options=[10, 20, 30]` publishes integer options; mixed lists retain mixed
number types. The input's `float` type alone does not choose the workaround.

This can affect a caller that reads a float-spelled manifest default and
sends it back through JavaScript's ordinary JSON serialization, which writes
a whole-number value as `20`, losing its float spelling. Writing `20.0` in
JavaScript before ordinary serialization does not preserve that distinction;
preserve each option's original number type at the HPO boundary. Upgrade to
HPO 0.11.1 or a later release containing the fix when available. This fix is
separate from the engine/codegen release; HPO 0.11.1 has not been released at
the time of writing.

### Recorded validation and grades

The landed #190 candidate recorded 9,254 engine-enabled pytest passes with
12 audited skips, 2,164 focused passes each under GCC and Clang without skips,
314 public corpus compilation passes, and 279 fixtures in each of the JSON
and Pyodide fixture-parity checks. Those
are the request's retained native measurements, not a new native suite run
for this documentation change. The release-pair baseline records
excellent: <!-- pf:releases[1.4.0].scoreboard.excellent|int -->7,983<!-- /pf -->;
strong: <!-- pf:releases[1.4.0].scoreboard.strong|int -->6<!-- /pf -->;
graded: <!-- pf:releases[1.4.0].scoreboard.graded|int -->7,989<!-- /pf --> probes,
with <!-- pf:releases[1.4.0].scoreboard.belowStrong|int -->0<!-- /pf --> below strong
and <!-- pf:releases[1.4.0].scoreboard.engineErrors|int -->0<!-- /pf --> engine errors.
These are recorded grading outcomes, not a statement that no internal attempt
ever failed. Baseline <!-- pf:releases[1.4.0].scoreboard.id|code -->`pineforge-parity-baseline-20261007-engine-b3192bfc`<!-- /pf -->
measures engine <!-- pf:releases[1.4.0].scoreboard.engineCommit|short-code -->`b3192bfc`<!-- /pf -->
and codegen <!-- pf:releases[1.4.0].scoreboard.codegenCommit|short-code -->`bfc4ddce`<!-- /pf -->;
these quantities render from the public release facts, not numeric overrides.

## 1.3.0 — 2026-10-06

A minor release: generated strategy libraries gain two receipts beside 1.2.0's
capability receipt, the confirmed-bar receipt, which engine `v1.3.0`'s live
runner reads, and the order-shapes receipt; a string dropdown that uses a
built-in constant no longer breaks the checked settings; and the package is
licensed under the PineForge Source License 1.2. It covers the changes merged
to `main` since 1.2.0:
[#173](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/173) and
[#178](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/178) add
the receipts to the emitted C++,
[#179](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/179)
changes its checked settings metadata,
[#177](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/177)
replaces the license, string values derived from a script are validated and
escaped wherever they are written into generated C++ (see "Hardening"),
[#174](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/174)
renders the README's scoreboard, and
[#175](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/175)
changes the repository's CI only. Codegen `1.3.0` supports only engine
`v1.3.0`. Engine `v1.3.0`'s `<pineforge/pineforge.h>` is the same file as
`v1.2.0`'s: the pair keeps C ABI version 4, `PF_SETTINGS_API_VERSION` and
`PF_CAPABILITIES_API_VERSION` 1 and the script ABI epoch
`engine_script_run_v19`, and no engine header declares the new receipt
functions, which a host looks up in the library by name.

### Compatibility and migration

- Regenerate C++ with 1.3.0 and relink it against engine `v1.3.0`'s headers
  and `libpineforge.a`. The C++ defines the two new receipts beside the
  capability functions, only when the engine's `pineforge/pineforge.h` defines
  both `PF_SETTINGS_API_VERSION` and `PF_CAPABILITIES_API_VERSION`, as engine
  `v1.3.0`'s does. Engine `v1.2.0`'s header defines both too; that does not
  make 1.3.0 with engine `v1.2.0` a supported pair. C++ generated by 1.2.0 has
  neither receipt, and engine `v1.3.0`'s live runner keeps its original
  receipt policy for it (see "Confirmed-bar receipt").
- Every generated C++ file changes: it gains the two receipts. Beyond that,
  the C++ does not change for the engine's 325 public corpus sources and this
  repository's 277 gate fixtures: each transpiled in a fresh process, 1.3.0's
  C++ with the confirmed-bar and order-shapes functions removed is 1.2.0's C++
  byte for byte, and the 13 fixtures the gate expects to be refused are
  refused with the same message. Elsewhere, #173 changes the capability
  receipt's request entries and `unresolved` list, and #179 the settings
  metadata of a string input that uses a built-in constant (see below),
  shapes none of those sources has. The trading code is unchanged.
- The Python and JSON contract does not change: no argument, result key or
  envelope is added, removed or renamed. Since 1.2.0 the package's code
  changed only in the code generator (`pineforge_codegen/codegen/`) and its
  support checker (`pineforge_codegen/support_checker.py`):
  `pineforge_codegen/__init__.py`, `pineforge_codegen/errors.py`,
  `gate/glue.py` and `pineforge_codegen/diagnostics_catalog.json` (628 codes)
  are 1.2.0's files. For the 602 sources above, each in a fresh process,
  `transpile_full()`'s result without `cpp`, the diagnostics' codes and
  arguments included, and the `transpile_json` envelope without `cpp` are
  1.2.0's. The GitHub release attaches the catalog as
  `diagnostics_catalog-v1.3.0.json`.
- Report keys: the engine's Docker harness (`docker/run_json.py`, which the
  `pineforge-release` image runs) is the same file in engine `v1.3.0` as in
  `v1.2.0`, so no report key is added, removed or renamed;
  `metrics.equity.sharpe_tv` and `sortino_tv` (`EQUITY_REPORT_KEYS`) are
  unchanged, and the engine's ADR-0001 keeps serialized report keys as they
  are. A report's fingerprint still differs between 1.2.0 and 1.3.0, since it
  records the engine and codegen versions and the generated C++'s hash, and
  the engine's Pine adapter changes since `v1.2.0`
  ([pineforge-engine#341](https://github.com/pineforge-4pass/pineforge-engine/pull/341),
  [#345](https://github.com/pineforge-4pass/pineforge-engine/pull/345),
  [#347](https://github.com/pineforge-4pass/pineforge-engine/pull/347)) can
  change a run's trades.
- 1.3.0 is the first release under the PineForge Source License 1.2. 1.2.0
  shipped under the PineForge Source License 1.1, and releases up to and
  including 1.1.0 under "PineForge Codegen — License"; copies of each release
  keep the license it shipped with (see "License").

### Confirmed-bar receipt

From [#173](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/173):

- Built against engine `v1.3.0`, a generated strategy library also defines
  `strategy_confirmed_bar_api_version()`, which returns 1, and
  `strategy_confirmed_bar_receipt(s, json, capacity, required, error,
  error_capacity)`, with the buffer protocol and statuses of
  `strategy_capabilities_receipt`. Its canonical JSON is written into the C++
  when it is generated, as the capability receipt's is, and has the keys
  `version`, `requests` (the capability receipt's request fields and the
  lowered `expression`), `orders` and `intrabar_persistence`. `orders` lists
  the distinct classified shapes of the script's `strategy.*` order calls,
  sorted: `entry:market`, `entry:stop`, `entry:limit`, `exit:short_bracket`
  and `close:market` for the modeled calls and arguments, and the call's name
  marked unproven, such as `strategy.exit (unproven exit terms)`, for any other
  call or argument, risk rules and an order ID that is not a string literal
  included. With `process_orders_on_close=true`, `strategy()` sizing, slippage
  or account settings outside the tested literal profiles add
  `strategy() (unproven POOC sizing, slippage or account settings)`.
- The capability receipt keeps its version and keys, but its request entries
  now take their timeframe, Heikin-Ashi flag and feed from the request's
  registration, as the confirmed-bar receipt's do. A timeframe held by a
  constant global (`htf = "D"`) or passed as a literal to a helper called once
  (`f("5")`) is that timeframe, where 1.2.0 wrote the name (`htf`) and listed
  `request.security[N].timeframe` in `unresolved`. A Heikin-Ashi request of the
  chart's symbol is `feed: "chart"`, where 1.2.0 wrote `auxiliary` and set
  `requirements.auxiliary_security_feeds`. Symbol `""`, which lowers through
  another symbol's feed, is `feed: "auxiliary"` and sets
  `requirements.auxiliary_security_feeds`, where 1.2.0 wrote `chart`. A request
  of `close`, `close[1]`, `ta.sma(close, 4)` or `ta.ema(close, 3)` in a script
  that declares its own `close` adds
  `request.security.expression (user-bound close)` to `unresolved`. An input,
  reassigned or other run-time timeframe stays unresolved.
- Engine `v1.3.0`'s live runner, `pineforge-live`, reads both receipts
  ([pineforge-engine#342](https://github.com/pineforge-4pass/pineforge-engine/pull/342)),
  checks that they agree and fails closed on a malformed field or a missing
  proof. With the confirmed-bar receipt it admits a same-chart request in the
  proven shapes the engine's `docs/strategy-capabilities.md` lists (such as
  `close` requested at `5`, `60` or `D`, on confirmed one-minute bars) and a
  standalone close-only `varip` on script clock `1`, where engine `v1.2.0`'s
  runner refused every request and every `varip`. Two requests together, a
  request with `varip`, and `process_orders_on_close=true` whatever the order
  shapes stay refused. A library without the confirmed-bar receipt, such as
  one generated by 1.2.0, keeps the original receipt's policy. A batch run
  reads neither receipt, and its computation is unchanged.

### Order-shapes receipt

From [#178](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/178):

- A generated strategy library also defines
  `strategy_order_shapes_api_version()`, which returns 1, and
  `strategy_order_shapes_receipt(s, json, capacity, required, error,
  error_capacity)`, with the same buffer protocol, whether
  `process_orders_on_close` is on or off. Its canonical JSON describes the
  strategy's order calls as they are lowered, for closing-time order
  processing: the keys are `version`, `process_orders_on_close`, `calls`,
  `entry_ids`, `host_reads`, `settings` and `unmodeled`. `calls` has one entry
  per lowered `strategy.entry`, `strategy.order`, `strategy.exit`,
  `strategy.close`, `strategy.close_all`, `strategy.cancel` or
  `strategy.cancel_all` site, in source order, classing each parameter the
  call passes: a number as `absent` (the default), `literal`, `never_na` or
  `maybe_na`, and text, enums, directions and IDs in classes of their own, an
  ID without its string; a site in a loop, a user function or a method is
  marked `repeatable`. `entry_ids` counts the IDs that `strategy.entry` and
  `strategy.order` sites set, by direction, shared and repeated; `host_reads`
  lists the execution members the generated C++ references; `settings` holds
  the compiled `strategy()` order settings (`pyramiding`, `default_qty_type`,
  `initial_capital`, `slippage`, ...), not later overrides; `unmodeled` names
  every other `strategy.*` call that is not read-only, risk rules included.
- The receipt describes the calls; it is not an admission decision. Engine
  `v1.3.0` does not read it: the runner-side policy that reads it ships in a
  separate engine change. #178 leaves the capability and confirmed-bar
  receipts byte for byte as they were, the confirmed-bar `orders` included
  (`tests/test_order_shapes.py` pins both), and the trading code unchanged.
  The [public contract](docs/PUBLIC_CONTRACT.md#optional-compiled-execution-capabilities)
  describes both new receipts.

### Checked settings

From [#179](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/179):

- A strategy whose `input.string` dropdown uses a built-in constant as its
  default or an option no longer breaks its checked settings. 1.2.0 wrote a
  display constant's integer code, such as `size.tiny`'s or
  `position.top_left`'s, where an option of the settings metadata needs a
  string, so building the metadata made a string from a null pointer and
  `strategy_get_effective_settings` or `strategy_set_input_checked` failed or
  crashed. A constant that the generated code reads as a string
  (`alert.freq_*`, `order.ascending` and `descending`, `session.regular` and
  `extended`, and the `currency.*` and `format.*` members, which publish their
  own names) keeps its exact default and choices. A default or option with no
  faithful string marks the input `"supported": false` in the settings
  receipt, with `"options": []` and, when the default is such a constant,
  `"default": ""`, where 1.2.0 published the input as supported;
  `strategy_set_input_checked` refuses it with `PF_SETTINGS_UNSUPPORTED`. The
  legacy `strategy_set_input`, the input getters and the strategy's execution
  are unchanged.

### Hardening

- String values derived from a script are validated and escaped wherever
  they are written into generated C++. The generated C++ of the 602 sources
  measured under "Compatibility and migration" does not change.

### License

From [#177](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/177):

- 1.3.0 is the first release under the PineForge Source License 1.2.
  Compared with 1.1 it changes two places and nothing else. In the Personal
  Trading section, an account that a proprietary-trading firm or a
  funded-trader program provides or allocates to the person to trade,
  including a challenge, evaluation or simulated account, whether the capital
  in it is real or simulated, is not the person's own account and is not
  funded by the person's own capital. In the Commercial Use section, the
  capital in such an account is investment capital whether it is real or
  simulated, so trading it, or researching, developing or backtesting
  strategies for it, is Investment Management, which `LICENSE` makes
  Commercial Use for every individual and every organization unless it is
  Personal Trading. 1.2.0 shipped under the PineForge Source License 1.1, and
  releases up to and including 1.1.0 under "PineForge Codegen — License", the
  PolyForm Noncommercial License 1.0.0 with PineForge's supplemental sections;
  copies of those releases keep the license they shipped with. `LICENSE` is the
  controlling text; `LEGAL.md` summarizes it and is not legal advice.
- Packaging: the PyPI classifier and `@pineforge/codegen-pyodide`'s
  `package.json` license field are 1.2.0's; the `LICENSE` both packages carry
  is version 1.2.

### Documentation

- The README's scoreboard of `main` renders from the facts tokens of
  `pineforge-release` at 3e39d64
  ([#174](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/174)):
  baseline `pineforge-parity-baseline-20261005-input-reanchor-781b8f00`
  (engine 3c414528, this repository at 39545379), 7,975 excellent and 14
  strong of 7,989 graded probes, none below strong.
  Release 1.3.0 is graded on registry baseline <!-- pf:releases[1.3.0].scoreboard.id|code -->`pineforge-parity-baseline-20261006-engine-7a1f01c0`<!-- /pf -->,
  which holds <!-- pf:releases[1.3.0].scoreboard.excellent|int -->7,982<!-- /pf --> excellent and <!-- pf:releases[1.3.0].scoreboard.strong|int -->7<!-- /pf --> strong of <!-- pf:releases[1.3.0].scoreboard.graded|int -->7,989<!-- /pf --> graded probes,
  and <!-- pf:releases[1.3.0].scoreboard.belowStrong|int -->0<!-- /pf --> below strong. It measured engine <!-- pf:releases[1.3.0].scoreboard.engineCommit|short -->7a1f01c0<!-- /pf -->
  ([pineforge-engine#347](https://github.com/pineforge-4pass/pineforge-engine/pull/347)'s
  merge), which v1.3.0 equals in behaviour, and this repository at <!-- pf:releases[1.3.0].scoreboard.codegenCommit|short -->3e50082f<!-- /pf -->
  ([#177](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/177)'s
  merge). The pr-gates of the later code changes, #178's receipts and the
  hardening of emitted string values, changed no graded probe, and the
  release's other changes are documentation.
- `docs/PUBLIC_CONTRACT.md` describes the confirmed-bar and order-shapes
  receipts ([#173](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/173),
  [#178](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/178)),
  and the README's License section, `LEGAL.md` and the npm README name the
  PineForge Source License 1.2
  ([#177](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/177)).

## 1.2.0 — 2026-10-05

A minor release: generated strategy libraries gain the compiled execution
capability receipt that engine `v1.2.0` adds to the C ABI, every diagnostic
carries a stable code and named arguments, and the package is licensed under
the PineForge Source License 1.1. It covers the changes merged to `main` since
1.1.0:
[#167](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/167)
changes the emitted C++,
[#166](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/166)
adds diagnostic codes to the Python and JSON results and raises a script's
first error in source order again,
[#171](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/171)
classifies diagnostic codes in linear time, moves to the PineForge Source
License 1.1 and renders the README's scoreboard, and
[#168](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/168)
replaces the license. Codegen `1.2.0` supports only engine `v1.2.0`. Engine
`v1.2.0` defines `PF_CAPABILITIES_API_VERSION` (1) and declares two functions
in `<pineforge/pineforge.h>`
([pineforge-engine#332](https://github.com/pineforge-4pass/pineforge-engine/pull/332))
that the C++ of codegen 1.2.0 defines; the pair keeps C ABI version 4 and the
script ABI epoch `engine_script_run_v19`.

### Compatibility and migration

- Regenerate C++ with 1.2.0 and relink it against engine `v1.2.0`'s headers
  and `libpineforge.a`. The C++ defines the capability functions only when the
  engine's `pineforge/pineforge.h` defines both `PF_SETTINGS_API_VERSION` and
  `PF_CAPABILITIES_API_VERSION`, as engine `v1.2.0`'s does. Against headers
  without `PF_CAPABILITIES_API_VERSION`, such as engine `v1.1.0`'s, that block
  compiles to nothing and the library has no receipt; this does not make such
  a pair supported. C++ generated by 1.1.0 has no receipt either. Engine
  `v1.2.0`'s live runner warns that it cannot prove a library without a
  receipt eligible, and runs it (see "Compiled execution capabilities").
- Every generated C++ file changes: it gains the capability block. Beyond
  that, the C++ does not change: for the engine's 325 public corpus sources
  and this repository's 277 gate fixtures, each transpiled in a fresh process,
  1.2.0's C++ with the capability block removed is 1.1.0's C++ byte for byte,
  and the 13 fixtures the gate expects to be refused are refused with the same
  message. A script name spelled `strategy_capabilities_api_version` or
  `strategy_capabilities_receipt` is renamed in the C++ (for example
  `pf_safe_strategy_capabilities_receipt`), so a script cannot shadow the new
  functions; input keys are unaffected.
- The Python and JSON contract is additive: each `Diagnostic` gains the
  properties `code` and `args`, `pineforge_codegen` exports
  `diagnostics_catalog()` and `render_diagnostic()`, and each JSON diagnostic
  of `gate/glue.py`'s `transpile_json` success and error envelopes gains the
  keys `code` and `args` (see "Diagnostic codes"). No argument, result key or
  envelope is removed or renamed: `transpile()` and `transpile_full()` keep
  their signatures and result keys, the envelopes keep every key they had, and
  each diagnostic keeps its `message`, `hint`, severity and location. One
  result changes: the error raised for a script with errors in more than one
  place (see "First error in source order").
- Report keys: the engine's Docker harness (`docker/run_json.py`, which the
  `pineforge-release` image runs) is the same file in engine `v1.2.0` as in
  `v1.1.0`, so no report key is added, removed or renamed;
  `metrics.equity.sharpe_tv` and `sortino_tv` (`EQUITY_REPORT_KEYS`) are
  unchanged, and the engine's ADR-0001 keeps serialized report keys as they
  are. A report's fingerprint still differs between 1.1.0 and 1.2.0, since it
  records the engine and codegen versions and the generated C++'s hash, and
  the engine's Pine adapter changes since `v1.1.0` (such as
  [pineforge-engine#330](https://github.com/pineforge-4pass/pineforge-engine/pull/330))
  can change a run's trades.
- 1.2.0 is the first release under the PineForge Source License 1.1. Releases
  up to and including 1.1.0 keep the license they shipped with (see
  "License").

### Compiled execution capabilities

From [#167](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/167):

- Built against engine `v1.2.0`, a generated strategy library defines
  `strategy_capabilities_api_version()`, which returns
  `PF_CAPABILITIES_API_VERSION` (1), and `strategy_capabilities_receipt(s,
  json, capacity, required, error, error_capacity)`, which writes the
  strategy's capability receipt. It uses the statuses and the buffer protocol
  of 1.1.0's `strategy_get_effective_settings`: called with a NULL buffer and
  0, it returns `PF_SETTINGS_BUFFER_TOO_SMALL` and the size to allocate, the
  NUL included; a short buffer gets an empty string, never partial JSON; a
  NULL strategy returns `PF_SETTINGS_INVALID_ARGUMENT`; no exception gets
  through. The base C ABI does not change.
- The receipt is canonical JSON (sorted keys, compact separators) written
  into the C++ when it is generated, not read from a running strategy: every
  handle returns the same bytes, before and after runs and setting changes.
  Version 1 has the keys `version`, `declarations`, `requests`,
  `requirements` and `unresolved`. `declarations` holds the `strategy()`
  values of `calc_on_every_tick`, `calc_on_order_fills`,
  `calc_on_every_history_tick`, `process_orders_on_close`,
  `use_bar_magnifier`, `fill_orders_on_standard_ohlc`,
  `backtest_fill_limits_assumption`, `currency`, `timeframe`,
  `timeframe_gaps` and `dynamic_requests`, Pine's defaults where omitted;
  positional arguments are read in Pine's parameter order. `requests` lists
  each `request.security` and `request.security_lower_tf` site, recorded
  request and request lowered to a run-time stop, with its function, symbol,
  timeframe, lookahead, gaps, Heikin-Ashi flag and feed (`chart`,
  `auxiliary`, `recorded` or `unpinned`). `requirements` says whether the
  script needs other symbols' feeds, an FX curve (a `currency` other than
  `currency.NONE`), recorded series or intrabar persistence (`varip`).
  `unresolved` names what the receipt cannot state as a literal (a nonliteral
  `strategy()` argument, a request's nonliteral timeframe or lookahead, a
  request lowered to a run-time stop) and every use of `barstate.isrealtime`,
  `timenow`, `barstate.islast`, `barstate.islastconfirmedhistory`,
  `last_bar_index` and `last_bar_time`, in plots, labels and tables too. The
  receipt proves declarations only, not that a stream computes what a batch
  run computes.
- A host reads the receipt before it runs a strategy, to tell whether its
  execution mode can honour what the strategy declares. Engine `v1.2.0`'s
  live runner, `pineforge-live`, reads it before it opens its ledger
  ([pineforge-engine#332](https://github.com/pineforge-4pass/pineforge-engine/pull/332))
  and refuses, naming the requirement, intrabar calculation
  (`calc_on_every_tick`, `calc_on_order_fills`, `calc_on_every_history_tick`),
  `process_orders_on_close`, the bar magnifier, standard-OHLC fills, nonzero
  limit-fill verification, a `currency` or a `timeframe` declared in
  `strategy()`, every true requirement, every request whatever its
  timeframe, and every name in `unresolved`. A library without the
  extension, such as one generated by 1.1.0, gets a warning that its
  eligibility cannot be proved, and runs, its requests included. A batch run
  does not read the receipt, and its computation is unchanged. The engine's
  `docs/strategy-capabilities.md` gives the schema and the runner's policy,
  and the
  [public contract](docs/PUBLIC_CONTRACT.md#optional-compiled-execution-capabilities)
  the codegen side.

### Diagnostic codes

From [#166](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/166):

- Every diagnostic carries a stable `code` (`PF-E1203` for an error,
  `PF-W0412` for a warning) and `args`, the named raw values (identifiers,
  types, keywords, numbers) its English `message` and `hint` were built from:
  in `transpile_full(...)["diagnostics"]`, in `CompileError.diagnostics` and
  in the JSON diagnostics of the `transpile_json` envelopes. They are read off
  the diagnostic's text when first asked for, after the transpile, so the
  emitted C++ does not depend on them; `message` and `hint` keep their text.
- `diagnostics_catalog()` returns the catalog, and `render_diagnostic(code,
  args)` the English `(message, hint)` a code renders, equal to the
  diagnostic's byte for byte. The catalog (schema
  `pineforge-diagnostics-catalog/v1`, 628 codes) gives each code its
  severity, area, ICU MessageFormat message and hint templates, argument
  kinds and a one-line explanation, so an application can translate a
  diagnostic by its code. It ships in the package as
  `pineforge_codegen/diagnostics_catalog.json`, in
  `@pineforge/codegen-pyodide` as the `./diagnostics_catalog.json` export,
  and with the GitHub release as `diagnostics_catalog-v1.2.0.json`.
- A code is never removed or reused, and a changed meaning gets a new code
  (`tests/fixtures/diagnostic_codes_pin.json`). See
  [Diagnostic codes](docs/PUBLIC_CONTRACT.md#diagnostic-codes).

### First error in source order

- 1.1.0's settings metadata read every input's `defval`, `options`,
  `minval`, `maxval` and `step` ahead of the script body, so an error there,
  such as an unknown name in a later input's `minval`, was raised before an
  error on an earlier line. 1.2.0 raises the script's first error in source
  order again, as 1.0.1 did
  ([#166](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/166)).
  The emitted C++ does not change.

### Security

- Classifying a diagnostic's code no longer stalls on crafted script text
  ([#171](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/171)).
  As #166 merged it, the classifier matched each catalog template as a
  backtracking regular expression over the message and hint, which carry text
  the script spells: 1.8 KB of one template's text took 0.55 s to classify
  and 3.4 KB took 23 s, a denial of service for `transpile_json`, which
  classifies every diagnostic it returns, and for any host that reads
  `code` or `args`. Classification now takes time linear in the text: the
  same text grown to 6.6 MB classifies in milliseconds, with the same codes
  and arguments. No release had the stall: it was on `main` from #166 to
  #171, and 1.1.0 has no diagnostic codes.

### License

From [#168](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/168)
and [#171](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/171):

- 1.2.0 is the first release under the PineForge Source License 1.1, whose
  licensor is pineforge, LLC. Releases up to and including 1.1.0 shipped
  under "PineForge Codegen — License", the PolyForm Noncommercial License
  1.0.0 with PineForge's supplemental sections, and copies of those releases
  keep that license. The PineForge Source License is not a PolyForm license.
  `LICENSE` is the controlling text; `LEGAL.md` summarizes it and is not
  legal advice.
- The permitted purposes, free of charge, are noncommercial purposes,
  personal uses, use by the noncommercial organizations `LICENSE` lists for
  their teaching, research and other operations, and Personal Trading: a
  natural person's research, development and backtesting of strategies and
  execution of trades for their own account with their own capital, as
  `LICENSE` defines them. Investment Management (managing, advising on or
  trading investment capital, researching, developing or backtesting
  strategies for it, or operating the software for others who do, whoever
  the capital belongs to and whether or not for a fee) is Commercial Use for
  every individual and every organization, noncommercial organizations
  included, unless it is Personal Trading. So is any other use that is not a
  permitted purpose, such as use by, for or on behalf of a company, fund,
  partnership or other organization. Commercial Use needs a commercial
  license from pineforge, LLC (enterprise@pineforge.dev).
- Distributing copies is not Commercial Use, except that distributing the
  software, changed or not, embedded in or bundled with a product or service
  made available to others is Commercial Use unless it is a permitted
  purpose. That exception is what 1.1 adds to the PineForge Source License
  1.0, which #168 put on `main` and no release carried.
- `LICENSE` defines Output: the code the software generates, such as the
  C++ it generates from PineScript, and any program or library built from
  it; backtest results, charts, reports and trade signals are not Output.
- Packaging: the PyPI classifier is `License :: Other/Proprietary License`
  (it was `License :: Free for non-commercial use`), and
  `@pineforge/codegen-pyodide`'s `package.json` says
  `"license": "SEE LICENSE IN LICENSE"` (it said
  `PolyForm-Noncommercial-1.0.0`) and the package contains `LICENSE`.

### Documentation

- The README's scoreboard of `main` renders from the facts tokens of
  `pineforge-release` at 03b8dcc
  ([#171](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/171)):
  baseline `pineforge-parity-baseline-20261004-engine-6b77f061` (engine
  6b77f061, this repository at 285ac035), 7,970 excellent and 19 strong of
  7,989 graded probes, none below strong.
  Release 1.2.0 is graded on registry baseline
  `pineforge-parity-baseline-20261005-engine-52292db9`: 7,970 excellent and
  19 strong of 7,989 graded probes, none below strong, with engine 52292db9
  ([pineforge-engine#339](https://github.com/pineforge-4pass/pineforge-engine/pull/339)'s
  merge), which v1.2.0 equals in behaviour, and this repository at 48e7a13b,
  whose emitted C++ is codegen 1.2.0's: the release's remaining changes are
  documentation.
- `docs/PUBLIC_CONTRACT.md` describes the capability functions
  ([#167](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/167))
  and diagnostic codes
  ([#166](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/166)).
  The README's License section and `LEGAL.md` name the PineForge Source
  License and pineforge, LLC and give enterprise@pineforge.dev for commercial
  licenses, and `CONTRIBUTING.md` names pineforge, LLC as the party a
  Contributor License Agreement grants rights to
  ([#168](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/168),
  [#171](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/171)).

## 1.1.0 — 2026-10-04

A minor release: generated strategy libraries gain the checked settings
functions that engine `v1.1.0` adds to the C ABI, and `transpile_full()` lists
the other symbols' feeds a script requests. It covers the changes merged to
`main` since 1.0.1:
[#159](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/159)
changes the emitted C++,
[#164](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/164)
adds request discovery to the Python and JSON results, and
[#160](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/160) and
[#162](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/162) the
documentation. Codegen `1.1.0` supports only engine `v1.1.0`. Engine
`v1.1.0` declares six functions in `<pineforge/pineforge.h>`
([pineforge-engine#317](https://github.com/pineforge-4pass/pineforge-engine/pull/317))
that the C++ of codegen 1.1.0 defines; the pair keeps C ABI version 4 and the
script ABI epoch `engine_script_run_v19`.

### Compatibility and migration

- Regenerate C++ with 1.1.0 and relink it against engine `v1.1.0`'s headers
  and `libpineforge.a`. The C++ includes `pineforge/checked_settings.hpp` and
  compiles the checked settings functions only when the engine's headers
  provide that file and define `PF_SETTINGS_API_VERSION` in
  `pineforge/pineforge.h`, as engine `v1.1.0`'s do. Engine `v1.1.0`'s library
  refuses to begin a run of a strategy whose setter failed (see "Exceptions at
  the C ABI"). C++ generated by 1.0.1 has neither the checked functions nor
  the exception handling.
- Every generated C++ file changes: it gains the settings code and the
  exception handlers described below. Beyond that, the C++ changes only for
  the enum defaults and the renamed names described below: for the engine's
  325 public corpus sources and this repository's 277 gate fixtures, 1.1.0's
  C++ with the settings code removed (`tests/_legacy_cpp.py`) is 1.0.1's C++
  byte for byte, and the 13 fixtures the gate expects to be refused are refused
  with the same message.
- Request discovery does not change the emitted C++
  ([#164](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/164)):
  the engine's 325 public corpus sources and this repository's 540
  test-fixture `.pine` files transpile to the same C++ as before it, byte for
  byte, each in a fresh process, and the 51 refused are refused with the same
  message.
- The Python and JSON contract is additive: `transpile_full()`'s result and
  `gate/glue.py`'s `transpile_json` success envelope gain the key `requests`,
  and each `input.symbol` entry of the input manifest gains
  `"kind": "symbol"` (see "Request discovery"). No argument, result key or
  envelope is removed or renamed, and `transpile()` and the error envelope are
  unchanged. One value changes: the `default` reported for an `input.enum`
  whose default is a variable (see "Enum input defaults").
- Report keys: the JSON report of the engine's Docker harness
  (`docker/run_json.py`, which the `pineforge-release` image runs) keeps every
  key it had with 1.0.1, `metrics.equity.sharpe_tv` and `sortino_tv` included,
  and adds `applied_runtime.syminfo`, `{"qty_step": v, "mincontract": v}`,
  present only when the `--syminfo` / `PINEFORGE_SYMINFO` file carries a valid
  `syminfo.mincontract`, which the harness now applies as the lot grid
  ([pineforge-engine#322](https://github.com/pineforge-4pass/pineforge-engine/pull/322));
  1.0.1 ignored it. `fingerprint.provenance.runtime` equals
  `applied_runtime`, so a run on such a grid has a different fingerprint; for a
  run without one, #322 changes no byte of the report apart from
  `elapsed_seconds`. A `mincontract` the harness cannot apply is new failure
  output: one stdout line `{"engine":"pineforge","error":...}`, harness exit
  status 1 and image entrypoint exit status 4 (the existing backtest-failure
  status), with `syminfo.mincontract must be a positive finite number, got
  <json>` for an invalid value, or `the strategy library has no
  strategy_set_syminfo_metadata, so syminfo.mincontract cannot be applied` for
  a library without that function.
- The same harness also installs other symbols' bars for `request.security`
  on another symbol: `--symbol-feeds <index.json>` (the image's
  `PINEFORGE_SYMBOL_FEEDS`), a JSON index keyed by the exact symbol string the
  script passes and by timeframe, each feed an OHLCV CSV, with the symbol's
  catalog `syminfo`
  ([pineforge-engine#327](https://github.com/pineforge-4pass/pineforge-engine/pull/327),
  [pineforge-release#23](https://github.com/pineforge-4pass/pineforge-release/pull/23)).
  It needs one feed per requested timeframe: nothing aggregates another
  symbol's bars. It installs them through `strategy_set_symbol_facts` and
  `strategy_set_symbol_feed`, C ABI calls since 1.0.0, so the generated C++
  does not change for it. What it installed is recorded as
  `applied_runtime.symbol_feeds`, `{"canonicalization": ..., "symbols":
  {"<symbol>": {"facts": {...}, "feeds": {"<timeframe>": {"bars": ...,
  "first_ts": ..., "last_ts": ..., "source_values_sha256": ...}}}}}`, present
  only when it installed a symbol, so such a run has its own fingerprint. An
  index or a feed it cannot install fails the run the same way: one stdout
  line `{"engine":"pineforge","error":"--symbol-feeds: ..."}`, harness exit
  status 1, image entrypoint exit status 4.
- Between 1.0.1 and 1.1.0 a report's fingerprint differs in any case, since it
  records the engine and codegen versions and the generated C++'s hash, and
  the Pine adapter changes in engine `v1.1.0`
  ([pineforge-engine#315](https://github.com/pineforge-4pass/pineforge-engine/pull/315),
  [#316](https://github.com/pineforge-4pass/pineforge-engine/pull/316)) can
  change a run's trades.

### Checked strategy settings

Built against engine `v1.1.0`, a generated strategy library defines
`strategy_settings_api_version()`, which returns 1, and five checked functions
([#159](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/159)).
Each returns a `pf_settings_status_t`, writes its message to an optional
caller buffer and lets no exception through. The legacy setters still accept
or ignore what they accepted or ignored, an unknown key included; a value that
makes one of them throw now fails the strategy (see "Exceptions at the C
ABI").

- `strategy_set_input_checked` takes the key the legacy getters read: the
  input's title, else its variable's name, else the empty key. Before it
  changes anything, it refuses an unknown key, a key two inputs share, an
  invalid boolean, enum member or option, trailing characters (`3x`), a
  fractional or out-of-range int (`5.5`, `2147483648.0`), `NaN` and `Inf`, and
  a value outside the input's `minval` / `maxval`. `step` is not enforced. An
  `input.enum` is supported only when its options are literal members of one
  enum.
- `strategy_set_override_checked` takes `initial_capital`,
  `commission_value`, `default_qty_value`, `pyramiding`, `slippage`,
  `process_orders_on_close`, `calc_on_order_fills`, `close_entries_rule`,
  `default_qty_type` and `commission_type`, with the aliases the legacy setter
  accepts (such as `strategy.cash`), and refuses an unknown key and an invalid
  enum value, boolean or number.
- Both setters refuse once a run has processed a bar or a stream has begun.
- `strategy_create_checked` refuses a nonempty `params_json`, which the legacy
  factory ignores.
- `strategy_get_effective_settings` writes a JSON receipt of every declared
  input, inline inputs included, in source order, and of the overrides above:
  type, options, default and effective value as canonical strings, and
  `min` / `max` / `step` as numbers or `null`. Called with a NULL buffer and 0,
  it returns `PF_SETTINGS_BUFFER_TOO_SMALL` and the size to allocate.
- `run_backtest_full_checked` runs the batch `run_backtest_full` runs and
  returns `PF_SETTINGS_RUN_FAILED` for a failure the run reports and
  `PF_SETTINGS_EXCEPTION` for an exception.
- Script names that the settings code uses (`config_`, `inputs_`,
  `override_`, `last_error`, `last_error_`, `script_bars_processed`,
  `stream_phase_`, `StreamPhase`, `checked_settings` and the `_pf_` helpers)
  are renamed in the C++, so a script cannot shadow that code. Input keys are
  unaffected.

The engine's `pineforge/pineforge.h` and `docs/checked-settings.md` give the
signatures, the statuses and the buffer rules.

### Exceptions at the C ABI

1.0.1's C++ let an exception thrown in a generated entry point propagate into
the host, where a C caller cannot catch it. From
[#159](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/159):

- `strategy_create`, `run_backtest`, `run_backtest_full`,
  `strategy_set_input`, `strategy_set_override`,
  `strategy_set_magnifier_volume_weighted`, `strategy_free` and `report_free`
  catch every exception. `strategy_create` then returns NULL; `run_backtest`
  and `run_backtest_full` zero the report and record `run_backtest: <message>`
  (or `run_backtest_full: …`) as the error `strategy_get_last_error` returns.
- A legacy setter that throws, such as
  `strategy_set_override(s, "pyramiding", "abc")`, fails the strategy for good.
  `strategy_get_last_error` returns `strategy_set_override: <message>`;
  `run_backtest` and `run_backtest_full` return an empty report;
  `run_backtest_full_checked`, the checked setters and
  `strategy_get_effective_settings` return `PF_SETTINGS_RUN_FAILED` with that
  message; `strategy_stream_begin` returns -1 and `strategy_last_run_status`
  1 (not completed). A call that clears the last error, such as feed
  configuration, does not clear the failure: the next run reports it again.
  Free the strategy and create a new one. A stream already running keeps the
  settings it began with. A legacy setter that does not throw behaves as
  before.
- With engine `v1.1.0`, `strategy_stream_begin` also fails with the message
  and status 1 when preparing the script throws for any other reason; a batch
  run keeps 1.0.1's empty report and completed status for such a failure
  ([pineforge-engine#317](https://github.com/pineforge-4pass/pineforge-engine/pull/317)).

### Enum input defaults

From [#159](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/159):

- A value that came out silently wrong: an `input.enum` whose default is a
  variable, as in `defaultSide = Side.long` followed by
  `input.enum(defaultSide, "Side")`, now defaults to that member. 1.0.1's C++
  read the variable before the script assigned it, so unless the host set the
  input, it held the enum's first member for the whole run.
- `transpile_full()`'s `inputs` and `transpile_json` report that member
  (`"Side.long"`) as the input's `default`, where 1.0.1 reported `null`.
- This holds for a top-level variable declared once before the input, never
  reassigned, and set to a literal member, directly or through another such
  variable. With any other variable, such as a `var` or one set by an
  expression (`c ? Side.long : Side.short`), the input reads it as 1.0.1's C++
  does; `strategy_set_input_checked` refuses such an input as unsupported, and
  the receipt reports its default as `na`.

### Request discovery

From [#164](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/164):

- `transpile_full()` returns a new key, `requests`: every request site that
  reads another symbol's feed, so a host can fetch the bars a run needs before
  it starts it. Each entry gives the request's `line` and `fn`, its symbol
  (`literal`, `input` with its override key and default, `computed` with its
  value at the inputs' defaults where literals and inputs compute it, or
  `unresolvable`), its timeframe (`literal` in the engine's feed spelling,
  `chart`, `input` or `computed`), `lookahead`, `gaps` and
  `ignore_invalid_symbol`. A request lowered to `na` (its value reaches only
  plots and alerts) is not listed. See the
  [public contract](docs/PUBLIC_CONTRACT.md#request-discovery).
- `gate/glue.py`'s `transpile_json` success envelope carries the same list
  under `requests`.
- The input manifest marks each `input.symbol` entry with `"kind": "symbol"`;
  its `title`, `type` and `default` are unchanged.

### Documentation

- The README's parity scoreboard renders from the public facts tokens of
  [`pineforge-release`](https://github.com/pineforge-4pass/pineforge-release),
  and a release's grades are stated apart from the scoreboard of `main`
  ([#160](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/160));
  no fact marker starts a line, so GitHub renders each sentence in its
  paragraph ([#162](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/162)).
  Release 1.1.0 is graded on registry baseline
  `pineforge-parity-baseline-20261004-engine-7b596622`: 7,951 excellent and
  38 strong of 7,989 graded probes, with engine 7b596622, which v1.1.0 equals
  in behaviour: v1.1.0 adds a performance fix that its merge gate shows
  changes no trade or report. This repository was at c5d97ee, whose emitted
  C++ is codegen 1.1.0's.
- `docs/PUBLIC_CONTRACT.md` describes the checked settings functions and the
  exception handling
  ([#159](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/159))
  and request discovery
  ([#164](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/164)).

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
