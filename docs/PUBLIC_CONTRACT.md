# Public contract for 1.0

The supported Python/JSON contract began in 1.0.0; the sections below identify
later additions. This revision includes the 1.4.0 pair (2026-10-08).
Pre-tag validation used codegen `bfc4ddce` and engine `b3192bfc`, whose
version files at that point read `1.3.0`.

Text marked **since 1.5.0** describes diagnostic additions that 1.4.0 and
earlier releases do not have: the `note` severity, the `user_message` template
and the eleven codes that became notes. 1.5.0 is the planned number of the
release that carries them; until that tag exists, this text describes the
source tree, not a published package.

## Optional compiled execution capabilities

Availability: **since 1.2.0**, with engine `v1.2.0`'s capability extension and
the receipt-based admission policy of its live runner.

The receipt proves declarations only, not general live-versus-batch equivalence.
Its `strategy()` positional arguments follow Pine signature order independently
of batch configuration extraction. Every unresolved argument is named in
`unresolved`. Runtime-lowered unpinned request sites retain their request kind,
symbol and timeframe in `requests` with `feed: "unpinned"`, and in `unresolved`.
The original receipt alone retains conservative request/POOC/varip refusals.
From 1.3.0 on, confirmed-bar order metadata is an allowlist of modeled strategy calls and
arguments. Risk rules and unmodeled order arguments are named as unproven;
POOC account/sizing/slippage declarations outside the tested literal profiles also
produce an unproven marker. This changes receipts only, never trading code.
From 1.3.0 on, libraries also export an independently versioned confirmed-bar receipt;
engine `v1.3.0`'s runner admits only its explicitly proven same-chart security shapes
and standalone close-only varip, not arbitrary requests or observed-tick calculation.
`process_orders_on_close=true` remains refused for every order shape and
request composition. Retained POOC order metadata and equivalence rows are
evidence for future admission, not admitted shapes; nullable price legs,
same-calculation brackets and empty entry IDs are not certified by the receipt.
Legacy libraries without a receipt warn and run, including their requests.
Realtime
builtins are conservatively named and refused: generated `barstate.isrealtime`
is `false`, and `timenow` reads the bar timestamp in both warmup and realtime.
`barstate.islast`, `barstate.islastconfirmedhistory`, `last_bar_index` and
`last_bar_time` are also named in `unresolved`: a stream's delivered endpoint
and last-bar flags differ from the completed batch's final bar.
All six builtin uses are refused including display-only use (plots, labels,
tables): the receipt records their use conservatively rather than certifying
that display-only code cannot influence strategy execution.

From 1.2.0 on, C++ compiled against headers that define both
`PF_SETTINGS_API_VERSION` and `PF_CAPABILITIES_API_VERSION`, as engine
`v1.2.0`'s do, adds `strategy_capabilities_api_version()` (version 1) and
`strategy_capabilities_receipt(handle, json, capacity, required, error, error_capacity)`.
The receipt uses the checked-settings status and buffer protocol: NULL/0 queries
return `PF_SETTINGS_BUFFER_TOO_SMALL`, `required` includes the NUL, and short
buffers return no partial JSON. Canonical JSON is emitted from `strategy()`
declarations and analyzed security/request sites, not guessed from a handle's
runtime behavior. It is immutable across settings changes and fresh/reused runs.

From 1.3.0 on, with engine `v1.3.0`, `strategy_confirmed_bar_api_version()` returns 1 and
`strategy_confirmed_bar_receipt(handle, json, capacity, required, error, error_capacity)`
uses the same immutable buffer protocol. Its JSON has `version`, `requests`
(including the expression), sorted `orders` classifications and
`intrabar_persistence`. Actual emitter `foreign`/`heikinashi` flags determine
feed classification and static registration metadata resolves constant and
single-call helper clocks. Symbol `""` is a foreign-feed requirement, not chart
data. Runtime/input/mutable timeframes remain unresolved.

No new fields are added to the original receipt: old runners ignore the extra
symbols and retain their original policy. A new runner paired with an old
library without these new symbols also retains its original admission policy.
These exports add metadata only; generated trading code is unchanged.

From 1.3.0 on, with engine `v1.3.0`, `strategy_order_shapes_api_version()` returns 1 and
`strategy_order_shapes_receipt(handle, json, capacity, required, error, error_capacity)`
uses the same checked-settings buffer protocol. Every generated strategy includes
this immutable receipt, whether closing-time order processing
(`process_orders_on_close`) is enabled or not. The declaration and constructor
settings echo describe the compiled defaults, not later input or setting overrides.

The canonical JSON contains `version`, `process_orders_on_close`, `calls`,
`entry_ids`, `host_reads`, `settings` and `unmodeled`. Each `calls` entry describes
one lowered Pine `strategy.entry`, `strategy.order`, `strategy.exit`,
`strategy.close`, `strategy.close_all`, `strategy.cancel` or `strategy.cancel_all`
site, in source order. It classifies each parameter that the call actually passes:
numeric values are default (`absent`), finite constants (`literal`), expressions
from a conservative non-missing-value allowlist (`never_na`), or potentially
missing (`maybe_na`). Text, enums, directions and IDs have separate classes.
IDs are classified without exposing their literal strings. Entry counts and
target relations include IDs from both `strategy.entry` and `strategy.order`;
they distinguish direction, shared IDs, repeated sites and exit ordering.
Loops and user functions or methods are marked `repeatable`.

Defaults are compared per lowered parameter, including the expanded optional
entry parameters. Dropped alert arguments do not affect the shape; an omitted
`from_entry` and `from_entry=""` both target entries globally. An exit lowered as
bracket cancellation records only its cancellation parameters; the receipt does
not relax existing Pine support checks. `host_reads`
lists generated execution-member references, excluding strings and comments;
`unmodeled` names other non-read-only `strategy.*` calls, including risk rules.
The receipt is a static description, not an admission decision or proof that an
arbitrary order shape is supported in a live run; engine `v1.3.0` does not read it.
Existing capability and confirmed-bar receipts, including their order
classifications, remain unchanged.

Version 1 has `version`, `declarations`, `requests`, `requirements` and `unresolved`
keys. Declarations include calculation cadence, close execution, magnifier,
standard-OHLC fill mode, limit verification, currency and clock settings. Requests
name their function, symbol, timeframe, gaps, lookahead, Heikin-Ashi transform and
feed source. Requirements name historical-only data and intrabar persistence;
nonliteral contexts remain explicit rather than silently assuming defaults.
See the paired engine's `docs/strategy-capabilities.md` for the exact schema and
live-runner refusal policy. No batch dispatch, strategy calculation, matching,
margin or numeric behavior changes. C++ compiled against headers without
`PF_CAPABILITIES_API_VERSION`, such as engine `v1.1.0`'s, has no capability
extension.

Timeframe spellings differ by surface, and both are stable. A capability
receipt records a request's `timeframe` as the script wrote it: a literal
verbatim (`"D"` stays `"D"`, `"1D"` stays `"1D"`), anything else as its Pine
expression (`timeframe.period`, a variable's name). [Request
discovery](#request-discovery) and feed keys use the engine's spelling, in
which Pine's bare `"D"` / `"W"` / `"M"` / `"S"` are `"1D"` / `"1W"` / `"1M"` /
`"1S"` and any other text is kept. Discovery folds `literal` and `computed`
values (`pineforge_codegen.request_discovery.canonical_timeframe`); the engine
folds the requested timeframe when it looks a feed up, so a script's `"D"` and
`"1D"` read the same feed, keyed `"1D"`. An `input` timeframe's `default` and
its override are raw: fold them the same way. To match a receipt's request to a
feed, fold the receipt's literal timeframe with that rule (folding twice
changes nothing); never map a feed key back to `"D"`.

## Optional coded run failures

Availability: **since 1.4.0**, with engine `v1.4.0`'s
`PINEFORGE_HAS_RUN_FAILURE_CODES_V1` feature. The feature is optional in emitted
source; that fallback is not an exception to exact release pairing.

Generated no-data/other-symbol, collection, UDT, unsupported-request and
checked-string stops carry compile-time provenance through coded helpers.
A script's `runtime.error` always selects `strategy_runtime_error`; neither
its message nor an English message copied from another failure selects a
code. Named `message` arguments are passed, and an omitted message stays empty.
`str.tonumber` evaluates its argument outside the numeric parser's narrow
catches so a run stop propagates; only numeric parsing failures become `na`.
The generated run wrappers retain inner codes. A legacy setter's first failure
latches its English and `setting_rejected` identity for subsequent begins.

The paired engine provides `strategy_get_last_error_code` and
`strategy_get_last_error_args` beside `strategy_get_last_error`; its run harness
adds `code` and typed `args` beside failure `error`. An empty English message
does not make the run successful or authorize use of its partial results.
Without the feature, generated shims retain the legacy exceptions and English.
This is a runtime failure channel, distinct from the `CompileError` and
`diagnostics` contract of transpilation. Regenerate and relink for the new pair;
C ABI version 4 alone does not authorize reusing a 1.3.0 strategy library.

The paired 1.4.0 harness uses the checked settings API for generated libraries;
the 1.3.0 harness used the legacy setters. Some requests that previously
returned success now fail before execution: a shared `Period` key set to `"7"`
is `setting_rejected` with reason `ambiguous_key`, textual color `"color.blue"`
is `expected_integer`, and `"15"` outside choices `[10, 20, 30]` is
`invalid_input_option`. The color manifest's `string` type and Pine-spelling
default do not describe the native setter's packed-integer encoding. Validate
presets against the compiled settings, including types, bounds and options;
the checked path also refuses unknown keys and invalid numeric, boolean or
enum values. An input with `supported: false` rejects a supplied value with
`setting_unsupported`; leave it at its compiled default.

These examples do not imply that every invalid request used to succeed.
`initial_capital="abc"` and `initial_capital="-1"` already failed on 1.3.0;
the paired engine now reports `setting_rejected` with
`expected_finite_decimal` and `value_below_minimum`, respectively.
`setting_rejected` is catalog class `input`; `setting_unsupported` is class
`unsupported`. A deliberate `runtime.error` is `strategy_runtime_error`,
class `strategy`. The installed engine entrypoint returns exit 4 for all
three codes: exit 4 alone does not select a billing or refund decision.

## Optional generated settings extension

From 1.1.0 on, C++ compiled against an engine providing
`pineforge/checked_settings.hpp`, as engine `v1.1.0` does, adds
`strategy_settings_api_version()` (version 1), `strategy_create_checked`,
`strategy_set_input_checked`, `strategy_set_override_checked`,
`strategy_get_effective_settings` and `run_backtest_full_checked` to generated
libraries. The existing symbols and their successful default computations are
unchanged. All generated factory, setter and batch entry points contain both
standard and non-standard exceptions; checked variants return a status and an
optional caller-buffer error instead of silently substituting defaults.

Checked inputs are keyed exactly as the generated getters: title, fallback
binding name, or the empty key. Metadata lists every declared input (including
inline inputs), its storage type, default, options, declared numeric limits and
UI step. Unknown/ambiguous keys, invalid booleans/enums/options, numeric suffixes,
integer overflow, non-finite numeric strings and limit violations are refused
before mutation. Legacy setters stay permissive. Receipts read the actual getters
and effective overrides, in source order, with canonical string values. Query
the required JSON buffer size with NULL/0 before reading it. Configure checked
settings before execution starts. The exact C signatures, statuses and buffer
contract are documented in the paired engine's `pineforge/pineforge.h` and
`docs/checked-settings.md`. Without that optional header, codegen still emits
the legacy ABI and exception containment, preserving old-engine builds.

A legacy setter that throws permanently invalidates that generated strategy
handle. Its first failure message is retained independently of the engine's
last-error buffer: auxiliary-feed configuration and other diagnostic-clearing
calls cannot clear the failure. Both batch and stream begins refuse execution,
and `strategy_get_last_error` reports the original setter failure. Legacy batch
reports remain empty; checked batch returns `PF_SETTINGS_RUN_FAILED`. Free and
recreate the handle to recover. Legacy setters that do not throw keep their
existing permissive behaviour.

Engines `v1.1.0`, `v1.2.0` and `v1.3.0`, the pairs of codegen 1.1.0, 1.2.0 and
1.3.0, provide that header. Settings
helper references are root-qualified and guarded by `PF_SETTINGS_API_VERSION`;
old headers retain the standard-exception fallback and legacy batch precheck.
Paired batch and stream refusals report NOT_COMPLETED through the shared native
begin. Checked setters and receipt queries on a latched handle also return
`PF_SETTINGS_RUN_FAILED`. An active stream keeps its begin-time settings after
a mid-stream legacy setter throws; the sticky failure applies to subsequent
begins.

Immutable identifier defaults for `input.enum` now resolve to their declared
member instead of reading an uninitialized script member (the old default was
incorrectly member zero). Both the getter and deterministic receipt use that
literal. Unfoldable defaults remain unsupported by checked setters and their
receipt uses `na` without reading script state; the legacy getter is unchanged.
Every enum option must be a literal member of one enum for checked support.
Stream begin fails on any script-preparation exception with its message and
NOT_COMPLETED; ordinary batch preparation failures keep their historical empty
report, diagnostic and completed status. Legacy exception text is standard-
library-dependent, and legacy run catches zero their output report.

## Request discovery

From 1.1.0 on, `transpile_full()`'s result and `gate/glue.py`'s
`transpile_json` success envelope carry a `requests` key, and each
`input.symbol` entry of the input manifest has `"kind": "symbol"` (its other
fields are unchanged). Request discovery does not change the emitted C++.

`requests` lists, in source order, every request site that reads another
symbol's feed: a `request.security` of another symbol whose value can reach a
trade. A run supplies such bars as one feed per (symbol string, timeframe) and
the engine matches both byte for byte, so each entry states them as the
generated code computes them before the first bar:

```json
{"line": 7, "fn": "request.security",
 "symbol":    {"kind": "input", "title": "Other symbol", "default": "BINANCE:ETHUSDT"},
 "timeframe": {"kind": "chart"},
 "lookahead": false, "gaps": false, "ignore_invalid_symbol": false}
```

| Field | Value |
| --- | --- |
| `line` | The 1-based line of the `request.security` call. A helper's request is listed once per symbol and timeframe its call paths pass, each at the request's line. |
| `fn` | The Pine function, `"request.security"`. |
| `symbol` | `{"kind": "literal", "value"}`: the exact key, exchange prefix and suffix kept (`"BINANCE:ETHUSDT.P"`). `{"kind": "input", "title", "default"}`: an `input.symbol`, `input.string` or string `input()` (`ticker.standard` / `ticker.inherit` of one too); the key is the run's value of the input, overridden under `title` (the manifest's override key), else `default`. `{"kind": "computed", "expr"[, "value"][, "inputs"]}`: `expr` spells the expression as written, `value` is its key at the inputs' defaults when literals and inputs compute it (string `+`, `==`, `!=`, `and`, `or`, `not`, `?:`; never the chart's own `syminfo.*` strings), and `inputs` names the override keys of the inputs it reads. `{"kind": "unresolvable", "expr"}`: a request whose symbol, timeframe or expression PineForge cannot key before the first bar; no feed serves it, and the run stops where its value is read. |
| `timeframe` | `{"kind": "literal", "value"}` in the engine's spelling: whole minutes (`"240"`), `<n>D`, `<n>W`, `<n>M` or `<n>S` (`"1D"`), Pine's bare `"D"` / `"W"` / `"M"` / `"S"` as `"1D"` / `"1W"` / `"1M"` / `"1S"`; other text is kept as written and no feed matches it. `{"kind": "chart"}`: `timeframe.period`, `timeframe.main_period` or `""`, the chart's timeframe (a run that reads another symbol's feed takes the chart's bars unaggregated). `{"kind": "input", "title", "default"}`: an input's raw value, spelled by the same rule, `""` the chart's. `{"kind": "computed", "expr"[, "value"][, "inputs"]}` as for the symbol, a `switch` of one-expression arms included (no matching arm and no default is the chart's); an empty `value` is the chart's timeframe. |
| `lookahead`, `gaps` | Booleans: `barmerge.lookahead_on`, `barmerge.gaps_on`. |
| `ignore_invalid_symbol` | `false` when omitted, else its value (at the inputs' defaults), `null` when not computable. |
| `column` | Present for a `request.footprint` payload: the feed column it reads (`"fp_delta_100_70"`). |

A request whose value reaches only plots, alerts, tables or logs is lowered to
`na` and reads no feed; a request in a helper nothing calls never runs. Neither
is listed. Nor are requests that read no other symbol's bars: the chart's own
symbol (`syminfo.tickerid`, `syminfo.ticker` and `ticker.*` of them), a
`request.security_lower_tf` of another symbol (the run stops where it is
evaluated), and `request.earnings` / `dividends` / `splits` / `financial`
(recorded series, not bars). A symbol string equal to the chart's at run time
reads the chart.

## Released contract

This is the contract that 1.0.0, released 2026-09-30, implements, and 1.0.1,
released 2026-10-02, implements unchanged. 1.1.0, released 2026-10-04,
implements it with the generated settings extension and request discovery
above added: `transpile_full()`'s result and the JSON success envelope gain
`requests`, and `input.symbol` manifest entries gain `kind`. No argument,
result key or envelope is removed or renamed. 1.2.0, released 2026-10-05,
implements it with the compiled execution capabilities and the diagnostic
codes described here added: each `Diagnostic` and each JSON diagnostic gain
`code` and `args`, and `pineforge_codegen` exports `diagnostics_catalog()`
and `render_diagnostic()`. No argument, result key or envelope is removed or
renamed. 1.3.0, released 2026-10-06, implements it unchanged, with the
confirmed-bar and order-shapes receipts described here added to the generated
C++: no argument, result key or envelope is added, removed or renamed.
The last 0.x release, 0.10.4, has
neither the `libraries` argument nor the `diagnostics` key described below.

The supported programmatic entry points are the Python functions
`pineforge_codegen.transpile` and `pineforge_codegen.transpile_full`, plus the
`gate/glue.py` `transpile_json` protocol shipped in the Pyodide package. The
pipeline classes shown in the [README](../README.md#advanced-run-the-pipeline-stages-directly)
are for advanced inspection and are not part of this 1.0 stability promise.

## Python functions

```python
transpile(pine_source: str, *, check_support: bool = True,
          filename: str = "<input>",
          libraries: Mapping[str, str] | None = None) -> str

transpile_full(pine_source: str, *, check_support: bool = True,
               filename: str = "<input>",
               libraries: Mapping[str, str] | None = None) -> dict
```

`transpile()` returns one complete C++ source string. It raises
`pineforge_codegen.errors.CompileError` for a located parse, support, analysis,
or generation error; its `diagnostics` attribute carries `Diagnostic` objects.
`filename` appears in their source locations and in `str(error)`.
`transpile()` returns no nonfatal diagnostics, neither warnings nor (since
1.5.0) notes; use `transpile_full()` to read them. The `diagnostics` of a
`CompileError` can carry them beside its errors.

`libraries` maps a Pine library's import path to its source
(`{"user/name/version": source_text}`); each import the script uses is inlined
from it before the support check. With `None`, the default, the sources are
read through the script's own requests manifest when `$PINEFORGE_PINE_LIBRARIES`
and `$PINEFORGE_REQUESTS_ROOT` name one. An import with no source is refused by
name, except one whose alias is `ta`, `math` or `str` and that names only that
namespace's built-ins, which is a no-op.

`transpile_full()` runs the same translation once and returns these keys on
success:

| Key | Python value | Meaning |
| --- | --- | --- |
| `cpp` | `str` | The generated source, identical to `transpile()` for the same inputs. |
| `inputs` | `list[dict]` | Input manifest in global source order, one entry per global-scope input call, including inline calls. |
| `strategyParams` | `dict` | Values extracted from the `strategy(...)` declaration; a nonliteral value can be `None`, and an omitted argument is absent even where the C++ applies a default. |
| `diagnostics` | `list[Diagnostic]` | Nonfatal diagnostics with source locations: warnings (`Level.WARNING`) and, since 1.5.0, notes (`Level.NOTE`). |
| `requests` | `list[dict]` | Since 1.1.0: the other symbols' feeds the script reads, one entry per request site; see [Request discovery](#request-discovery). |

An error still raises `CompileError`; there is no partial success dict. The
diagnostic severity enum values are `"warning"`, `"error"` and, since 1.5.0,
`"note"` ([Diagnostic codes](#diagnostic-codes)). A successful
`transpile_full()` returns warnings and notes; a `CompileError` can carry
errors together with either nonfatal level.

### Input manifest and override keys

Each manifest entry has `title` (string), `type` (one of `int`, `float`,
`bool`, `string`, `source`, `enum`), `default` (a literal scalar or `None`) and
`supported` (boolean; the manifests of releases up to 1.3.0 lack it). It may
also have `min`, `max`, `step` numeric values and an `options` list. Since 1.1.0,
an `input.symbol` entry also has `kind` equal to `"symbol"`. An optional field
is omitted when its argument is absent or cannot be reduced to the supported
literal form.

From 1.4.0, `supported` and the values below come from the checked-settings
descriptor, subject to the explicit retained limits below (what
the compiled strategy's `strategy_get_effective_settings` reports): the manifest
is read from the descriptor that generates the receipt, and PyPI's
`transpile_full()` and the npm package's Pyodide/glue success envelope carry
them identically.

- `supported` is `false` for an input the compiled strategy cannot honour: a
  string input whose default or a choice is neither a string literal nor a known
  built-in constant (`size.small`, `position.top_right`, `na`, a reassigned
  name), or an `input.enum` whose default or choices are not literal members of
  one enum. Hide such inputs, exclude them from sweeps and optimization, and
  do not send a value: the strategy uses its compiled default, and the paired
  engine rejects a supplied value with `setting_unsupported`. Use `supported`
  to determine eligibility rather than `options` emptiness: every unsupported
  string input has `options: []`, with or without `options=`, and so does a
  supported input written `options=[]`. A true flag does not validate a value's
  type, bounds or choices, or detect duplicate override keys.
- A string input (`input.string`, `.timeframe`, `.session`, `.symbol`, ...)
  publishes the lowered strings: a built-in constant its runtime value
  (`alert.freq_all` is `"all"`, `currency.USD` `"USD"`, `format.price`
  `"price"`, `order.ascending` `"ascending"`, `session.regular` `"regular"`) and
  a named string constant its value. An unrepresentable default is `""`; a
  representable default is kept when only a choice is unsupported. The
  descriptor decodes only the emitter's C++ string-literal escape grammar,
  preserves literal Unicode and escaped backslashes, and stops at the first
  embedded NUL, matching native `std::string(const char*)` construction. The
  emitter spells NUL as fixed three-digit octal `\000` (one backslash), including before
  digits. This is current manifest/receipt consistency, not lossless storage
  of a string containing NUL.
- A source input (`input.source(hl2)`, a plain `input(close)`) publishes its
  series as `default` and the nine native sources (`close`, `high`, `hl2`,
  `hlc3`, `hlcc4`, `low`, `ohlc4`, `open`, `volume`) as `options`; a plain
  `input(close)` keeps its `string` type.
- An enum input publishes its `Enum.member` choices in declaration order as
  `options` and keeps its `Enum.member` `default`, which the receipt encodes as
  the member's index (map it through the receipt's `option_values`).
- A typed numeric input (`input.int`, `.float`, `.price`, `.time`) publishes
  numeric `default`, `min`, `max`, `step` and the numeric choices of an
  `options=[...]` dropdown where the receipt's value is a literal: a signed number (`-2.5`), a named constant (`LEN = 14`,
  `minval=-RATIO`) or `timestamp("2024-01-02T00:00:00")` of a string literal. A
  plain `input(-5)` or `input(-2.5)` is typed `int` or `float` by its literal;
  these signed forms retain a double getter and a `float` receipt type.
  A plain `input(5)` uses an int getter and receipt.
  An `input.bool` default is a boolean when the
  descriptor lowers it to literal `true` or `false`, including an inlined
  constant; `not true` remains a run-time expression.
- Not published, because the receipt computes it at run time: arithmetic over
  constants (`LEN * 2`), a call (`math.pow(2, 3)`), `timestamp(year, month,
  ...)` (it reads the symbol's time zone) and `not true`. Such a `default` is
  `None` and such a `min` or `max` is omitted. The whole numeric `options`
  field is omitted when any choice is unfoldable. These gaps are pinned in
  `tests/test_input_metadata_receipt.py`'s `KNOWN_LIMITS`; its test view maps
  an absent options field to `[]`, which is not the public manifest shape.
  An `input.color`'s
  `default` is its Pine spelling (`"color.red"`) and its `type` `string`, where
  the receipt holds the packed integer required by the checked native setter.
  Do not submit the manifest's Pine-spelling string as a native color value.
  Apart from the source-input case
  above, a plain `input(...)` whose default is not a literal is typed
  `string` with a `None` default.

No key is renamed or removed and the override keys are unchanged; the values
above are new or corrected values of existing keys, listed in the changelog.
The `title` is the **actual override key read by the emitted C++**:

1. The value of an explicit compile-time constant `title`, if supplied.
2. Otherwise, the name of the declaration containing the input call.
3. Otherwise, the empty string `""`.

For example, `length = input.int(14)` has key `"length"`, and
`ta.ema(close, input.int(9, "Fast"))` has key `"Fast"`. The manifest includes
both declared and inline global-scope calls, one row per call. If several
calls share a title or fallback key, those rows retain the same `title` and
their per-input `supported` flags do not reflect the collision. The translator
warns about the colliding inputs. The legacy setters apply a shared override
to all matching inputs; the checked setters refuse the key as ambiguous.
On the paired 1.4.0 engine's normal checked path, a supplied shared key fails
with `setting_rejected`, reason `ambiguous_key`; a completed run with no such
override still reports the whole `fingerprint` as `null`. This differs from
legacy-path unresolved provenance rows, which may remain inside a fingerprint.
Give each input a unique title. Different `group=` labels do not separate keys.
An explicit title that is not a compile-time string constant raises
`CompileError`.

## Pyodide and gate/glue JSON

`gate/glue.py` defines `transpile_json(source: str) -> str`. It returns a
serialized JSON **string**. The shipped Pyodide worker runs that glue. Its
success and compile-error envelopes are:

```json
{"ok":true,"cpp":"...","inputs":[],"strategyParams":{},"diagnostics":[],"requests":[]}
```

```json
{"ok":false,"error":"<input>:2:1: ...","diagnostics":[{"line":2,"col":1,"message":"...","severity":"error","endCol":10}]}
```

`ok` is a boolean. On success, `cpp`, `inputs`, `strategyParams` and (since
1.1.0) `requests` have the same meanings as in `transpile_full()`, and
`diagnostics` contains warnings and, since 1.5.0, notes. On a `CompileError`,
`error` is `str(error)` and `diagnostics` contains the error's diagnostics,
nonfatal ones collected with the errors included; `cpp`, `inputs`,
`strategyParams` and `requests` are absent. Every JSON diagnostic has 1-based
integer `line` and `col`, a `message` string, and `severity` equal to
`"warning"`, `"error"` or, since 1.5.0, `"note"`. `endCol` is included when the
source location provides it. A diagnostic hint, when present, is appended to
`message` after ` — `. Since 1.2.0 every JSON diagnostic also has `code` and
`args`; since 1.5.0 it also has `user_message`, the short ICU template
([Diagnostic codes](#diagnostic-codes)). The glue catches
`CompileError`; an unexpected Python exception may propagate instead of
producing an envelope. The JSON entry point does not accept a `filename`
argument.

## Diagnostic codes

Every `Diagnostic` (in `transpile_full(...)["diagnostics"]`, in a
`CompileError`'s `diagnostics`, and in the glue envelopes) carries the stable
`code` and `args` introduced in 1.2.0. Since 1.5.0 it also carries
`user_message`, and its severity can be `note`:

- `code`: a stable string `PF-<S><NNNN>`. `E` and `W` are historical identity
  prefixes, not the severity: the eleven codes that became notes in 1.5.0
  keep their `PF-W` codes. Its first digit is the area that spells the text: `0`
  source (lexer, parser, limits), `1` support checker and requests, `2`
  analysis, `3` `request.security`, `4` libraries and imports, `5` code
  generation, `6` array and matrix history, `7` `ta.*`. The support checker
  reports what it refuses inside a `switch` arm as a warning: `PF-W1nnn` with
  `nnn` below 500 is `PF-E1nnn` there.
- Severity (`Diagnostic.level`, the `severity` of a glue diagnostic and of a
  catalog entry): `"error"`, `"warning"` or, since 1.5.0, `"note"`. An error is
  fatal and raises `CompileError`. A warning and a note are nonfatal: a script
  that transpiles with either returns its C++ (`ok: true` in the glue
  envelope), and a `CompileError` can carry them beside its errors. The catalog
  declares each code's severity and the diagnostic carries it; read it, never
  infer it from the `E` / `W` prefix or from the words of a message ("visual
  only", "display"). A note is a classification, not a statement that results
  are unaffected: PF-W1505, a note, still describes a warmup approximation.
- `args`: an object of named values, raw: identifiers, types, keywords and
  Pine spellings as strings, counts as JSON numbers; never quoting, backticks
  or a formatted number.
- `user_message` (since 1.5.0): one short English ICU **template**, identical
  on the catalog entry, `Diagnostic.user_message` and the JSON diagnostic. It
  uses only the diagnostic's existing `args` names and may be constant. It is
  the raw template over `args`, not pre-rendered English; it does not replace
  `message` and `hint`, and it never substitutes the unbounded raw diagnostic
  as its whole sentence. For example, `"{name} is not drawn in backtests."`
  travels with `args.name = "plot"`; a receiver renders or translates it.

`diagnostics_catalog()` returns the catalog, which ships as
`pineforge_codegen/diagnostics_catalog.json` (schema
`pineforge-diagnostics-catalog/v1`, whose id the 1.5.0 additions do not
change) and is attached to each GitHub release
(`diagnostics_catalog-v1.3.0.json` for 1.3.0).
Per code it gives `severity` (`error`, `warning` or, since 1.5.0, `note`),
`area`, the English ICU MessageFormat `message` template, the `hint` template
or `null`, a one-line `explanation`, `args` and, since 1.5.0, the short
`user_message` template. `args` gives each argument's
`kind` — `identifier`, `type`, `keyword`, `number`, `vocab`
(an English word or phrase the transpiler picks from the closed set listed in
`values`, which an application may translate) or `text` (open English text the
transpiler builds, such as a nested reason; shown as is). The templates use
simple `{name}` arguments and ICU apostrophe quoting (`''` is one apostrophe,
`'{'` a literal brace); a string argument renders as is and a number in plain
decimal digits. `render_diagnostic(code, args)` returns the English
`(message, hint)`, which equal the diagnostic's `message` and `hint` byte for
byte; in the glue envelope `message` is the message, plus ` — ` and the hint
when there is one.

From 1.5.0 on, receivers translate by stable code using their own locale
catalogs; the English `user_message` template is the translation source and
fallback for an unknown code. An unknown code is shown at its declared
recognized severity. An unknown severity is read as `warning`, including when
the code is known; code prefixes do not override that rule. Render the
template with the separate argument values as text, not as new template syntax
or markup.
The receiver examples in `tests/fixtures/diagnostic_notes/receiver.json`
specify this compatibility rule; they are not evidence of an app deployment.

Since 1.5.0 the reviewed warning-to-note migration is exactly PF-W1505,
PF-W1508, PF-W1509, PF-W1525, PF-W1526, PF-W1527, PF-W1528, PF-W1529,
PF-W1530, PF-W1531 and PF-W1532, each a warning in 1.4.0. All other
severities, all stable codes and every existing message, hint, explanation and
argument definition are unchanged, and no existing key of a `Diagnostic`, a
JSON diagnostic, the catalog or the envelopes is renamed or removed: `severity`
gains the value `note` and `user_message` is added, while `code`, `args`,
`message`, `hint`, `line`, `col` and `endCol` keep their values.
The prior catalog and pin remain explicit legacy fixtures beside the exact
delta in `tests/fixtures/diagnostic_notes/`. PF-W1509 remains a legacy
template alias: the existing text classifier selects PF-W1508 when those
two templates tie. The migration does not invent a new emitted identity.

Those eleven are the only codes the catalog declares as notes; a message that
says "visual only" or "display" does not make its code one. PF-W1508 and
PF-W1509 describe a call PineForge skips as visual only (`plot(...)`,
`table.new(...)`). PF-W1525 to PF-W1532 describe an earnings, dividends,
splits, financial, footprint or other-symbol request whose value reaches only
display and alert sinks and is lowered to `na`. PF-W1505 describes the
`ta.ema` warmup approximation, a note by the explicit acceptance of the
initialization-note foundation. Five diagnostics that also speak of
visual-only or display-only handling remain warnings: PF-W1502 (a `color(...)`
cast), PF-W1506 (`color.from_gradient`), PF-W1507 (a drawing setter that drops
color, style or size), PF-W1078 (a visual or style constant) and PF-W1552 (a
`request.security_lower_tf` whose symbol can be another symbol's and whose
value reaches only display and alert sinks). Every other diagnostic keeps its
declared severity as well: executable missing feeds, partial risk support,
ignored trading values, repainting, numerical approximations, lossy values and
invalid arguments stay warnings or errors.

PF-W1505 is a static note: the support checker emits it at each `ta.ema` call
it visits, with no condition on the run, an input or the timeframe, and nothing
in this revision suppresses or qualifies it per call. It still carries its
original compile-time EMA caveat. This foundation does not establish startup
adequacy or change seeding. The selected-window consumer may show its startup
sentence only for a known finite shortfall in bars of that site's
evaluation-context timeframe; absence is not parity. No sidecar exists yet, at
compile time or at run time: this revision emits no `call_site_id` and no
startup fact. The subsequent startup inventory and extraction must bind a
versioned sidecar to Pine/generated identities, and share `call_site_id` and
the exact source range with the diagnostic rather than joining by text; until
that work lands, the static notes stay as they are. Input-bound lengths retain
the input name or expression; runs/trials resolve submitted values and studies
retain searched ranges. Recursive adequacy and seeding facts remain separate
and may be unknown. The typed consumer fixtures (`warmup_consumer.py` and
`warmup_handoff.json` in `tests/fixtures/diagnostic_notes/`) describe that
later handoff; they are contract examples, not transpiler output.

A code is never removed or reused, and a changed meaning gets a new code
(`tests/fixtures/diagnostic_codes_pin.json` pins each code's severity,
`message` and `hint` templates and argument names; it does not pin
`user_message`). Since 1.5.0 the `user_message` templates of the eleven
migrated codes are fixed by `tests/fixtures/diagnostic_notes/delta.json`; the
template of every other code is checked for form (a short, parseable sentence
over the entry's own argument names), not for its wording. A text no template
renders carries `PF-E0000` / `PF-W0000` with the text in `args.message` (and
`args.hint`); the test suite refuses it. The `message` text itself is
unchanged and stays the English rendering; `runtime.error` text a strategy
authors is not a transpile diagnostic. Since 1.5.0 an uncatalogued note uses
the existing PF-W0000 fallback with its actual `note` level preserved;
PF-W0000's catalog severity remains `warning`. The diagnostic level is the
wire authority in that fallback case. No new fallback code is allocated.

`scripts/gen_diagnostics_catalog.py --write` adds identities for newly
spelled templates and preserves existing pins. Since 1.5.0 new entries require
an authored `user_message`; the generator reports missing, malformed or
unknown-argument presentation templates rather than copying raw diagnostics.
The eleven reviewed severity pin changes above are explicit fixture-backed
exceptions, not a general permission to regenerate existing pins.

## Compatibility boundary

`check_support=False` on either Python function is **experimental**. It skips
the support gate and can produce C++ that fails to compile or does not match
Pine behavior; the 1.0 compatibility promise applies with the default
`check_support=True`.

There is no installed command-line interface. This package makes no
process-exit-code promises for a CLI, shell wrapper, or gate script. Consumers
should use the Python exception and return-value contract or the JSON
`ok`/`diagnostics` contract.

Generated C++ has a separate runtime pairing requirement: from 1.0.0 on,
codegen `X.Y.Z` supports only engine `vX.Y.Z` with that release's generated
headers and static library; prerelease tags match exactly. (The 0.x releases
pair by the `pineforge-release` image's record instead; see the README.)
Regenerate C++ and relink strategy libraries on every pair change.
`PF_ABI_VERSION` equality alone is insufficient. See
[CONTRIBUTING.md](../CONTRIBUTING.md#engine-pairing).
