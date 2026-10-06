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

- Generated C++ no longer depends on memory layout: requested-context history
  resets and TA variant position ties preserve their traversal order. Refusal
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
- The transpile-time input manifest (`transpile_full()["inputs"]` and the JSON
  envelope of the Pyodide package) gains `supported`, the checked-settings
  receipt's flag, on every input, and reads its defaults, choices and bounds
  from the descriptor that generates the receipt, so the two agree for every
  input of the public corpus and the repository's fixtures. Generated C++,
  `strategyParams` and `requests` are unchanged and no key is renamed or
  removed, but these values of existing keys change:
  - Source inputs (`input.source`, a plain `input(close)`): `default` is the
    series name, where it was `null`, and `options` lists the nine native
    sources, where it was absent.
  - Enum inputs: `options` lists the `Enum.member` choices, where it was
    absent.
  - String inputs: a built-in constant publishes its runtime value
    (`alert.freq_all` is `"all"`, `currency.USD` is `"USD"`; the manifest held
    the Pine names), a named string constant its value, and the choices of
    `input.timeframe` and `input.session` are listed. What the receipt cannot
    represent (`size.small`, `position.top_right`, `na`) is `default: ""`,
    `options: []` and `supported: false`.
  - Typed `int`, `float`, `price`, `time` and `bool` inputs: a signed or
    named-constant `default` is a number, where it was `null`; `min`, `max` and
    `step` appear for signed and named-constant bounds (`minval=-80` was left
    out); an `options=[...]` dropdown lists its numbers.
  - A plain `input(-5)` or `input(-2.5)` is typed `int` or `float` with its
    number as `default`, where it was `string` with `null`.

  Values the receipt computes at run time (`LEN * 2`, `not true`,
  `timestamp(year, month, ...)`, a color) stay unpublished, as before.
- Documents the timeframe spellings of two surfaces, both stable: a capability
  receipt records a request's timeframe as the script wrote it (`"D"`), and
  request discovery and feed keys use the engine's spelling (`"1D"`).
- Bind keyword arguments of the matrix methods and the checked array methods to their own parameter slot, with the omitted optional parameters taking their defaults in place (`matrix.sort(m, order = order.descending)`, `m.submatrix(to_column = 1)`, `a.fill(7.0, index_to = 1)` no longer drop or shift a keyword), and refuse a keyword that names no parameter, or a parameter given twice, with the existing argument diagnostics. A defaulted `submatrix` evaluates its receiver once, an index-only `add_row` / `add_col` takes any numeric index, and a bound `array.concat` result (`c = a.concat(b)`, `c := array.concat(a, b)`) keeps the unsupported-function refusal. An array history that reaches a user function's or method's parameter keeps its earlier copy lowering and the earlier refusal of a callee that checks it with `na()`; a bound missing-history matrix reports the recorded na-ID runtime error.
- Compile matrix/matrix and matrix/scalar `matrix.sum` overloads with matrix-valued results, including helpers and history. Support scalar `matrix.diff`/`matrix.mult` and omitted optional arguments to matrix row/column insertion, submatrix, sort and array fill; unsupported matrix/vector multiplication and bound `array.concat` results report an existing unsupported-function diagnostic instead of failing C++ compilation.
- Preserve na array IDs when a history offset has no value, including aliases and `na()`-only bindings; array methods on those IDs report the existing runtime error instead of crashing. Bounds-check legacy collection-element reads and writes to prevent unchecked access.

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
