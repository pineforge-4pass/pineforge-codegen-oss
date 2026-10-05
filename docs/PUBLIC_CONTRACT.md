# Public contract for 1.0

## Optional compiled execution capabilities

Availability: **since 1.2.0**, with engine `v1.2.0`'s capability extension and
the receipt-based admission policy of its live runner.

The receipt proves declarations only, not general live-versus-batch equivalence.
Its `strategy()` positional arguments follow Pine signature order independently
of batch configuration extraction. Every unresolved argument is named in
`unresolved`. Runtime-lowered unpinned request sites retain their request kind,
symbol and timeframe in `requests` with `feed: "unpinned"`, and in `unresolved`.
The original receipt alone retains conservative request/POOC/varip refusals.
After 1.2.0, confirmed-bar order metadata is an allowlist of modeled strategy calls and
arguments. Risk rules and unmodeled order arguments are named as unproven;
POOC account/sizing/slippage declarations outside the tested literal profiles also
produce an unproven marker. This changes receipts only, never trading code.
New libraries (unreleased; after 1.2.0) also export an independently versioned confirmed-bar receipt;
the paired runner admits only its explicitly proven same-chart security shapes
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

Unreleased (after 1.2.0): `strategy_confirmed_bar_api_version()` returns 1 and
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

Unreleased (after 1.2.0): `strategy_order_shapes_api_version()` returns 1 and
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
arbitrary order shape is supported in a live run. Existing capability and
confirmed-bar receipts, including their order classifications, remain unchanged.

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

Engines `v1.1.0` and `v1.2.0`, the pairs of codegen 1.1.0 and 1.2.0, provide
that header. Settings
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
renamed.
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
`transpile()` does not return nonfatal warnings.

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
| `diagnostics` | `list[Diagnostic]` | Nonfatal warnings only, with `Level.WARNING` and source locations. |
| `requests` | `list[dict]` | Since 1.1.0: the other symbols' feeds the script reads, one entry per request site; see [Request discovery](#request-discovery). |

An error still raises `CompileError`; there is no partial success dict. The
diagnostic severity enum values are `"warning"` and `"error"`. A successful
`transpile_full()` returns only warnings; a `CompileError` can carry both
warnings and errors.

### Input manifest and override keys

Each manifest entry has `title` (string), `type` (one of `int`, `float`,
`bool`, `string`, `source`, `enum`), and `default` (a literal scalar or `None`).
It may also have `min`, `max`, `step` numeric values or a string `options`
list. Since 1.1.0, an `input.symbol` entry also has `kind` equal to
`"symbol"`. An optional field is omitted when its argument is absent or cannot be
reduced to the supported literal form. The `title` is the **actual override
key read by the emitted C++**:

1. The value of an explicit compile-time constant `title`, if supplied.
2. Otherwise, the name of the declaration containing the input call.
3. Otherwise, the empty string `""`.

For example, `length = input.int(14)` has key `"length"`, and
`ta.ema(close, input.int(9, "Fast"))` has key `"Fast"`. The manifest includes
both declared and inline global-scope calls. If several calls share a key,
one override sets all of them; the translator emits a warning naming the
colliding inputs. Different `group=` labels do not separate override keys.
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
`diagnostics` contains warnings. On a `CompileError`, `error` is `str(error)`
and `diagnostics` contains the error's diagnostics; `cpp`, `inputs`,
`strategyParams` and `requests` are absent. Every JSON diagnostic has 1-based
integer `line` and `col`, a `message` string, and `severity` equal to
`"warning"` or `"error"`. `endCol` is included when the
source location provides it. A diagnostic hint, when present, is appended to
`message` after ` — `. Since 1.2.0 every JSON diagnostic also has `code` and
`args` ([Diagnostic codes](#diagnostic-codes)). The glue catches
`CompileError`; an unexpected Python exception may propagate instead of
producing an envelope. The JSON entry point does not accept a `filename`
argument.

## Diagnostic codes

Since 1.2.0 every `Diagnostic` (in `transpile_full(...)["diagnostics"]`, in a
`CompileError`'s `diagnostics`, and in the glue envelopes) carries:

- `code`: a stable string `PF-<S><NNNN>`, `S` being `E` for an error and `W`
  for a warning. Its first digit is the area that spells the text: `0`
  source (lexer, parser, limits), `1` support checker and requests, `2`
  analysis, `3` `request.security`, `4` libraries and imports, `5` code
  generation, `6` array and matrix history, `7` `ta.*`. The support checker
  reports what it refuses inside a `switch` arm as a warning: `PF-W1nnn` with
  `nnn` below 500 is `PF-E1nnn` there.
- `args`: an object of named values, raw: identifiers, types, keywords and
  Pine spellings as strings, counts as JSON numbers; never quoting, backticks
  or a formatted number.

`diagnostics_catalog()` returns the catalog, which ships as
`pineforge_codegen/diagnostics_catalog.json` (schema
`pineforge-diagnostics-catalog/v1`) and is attached to each GitHub release
(`diagnostics_catalog-v1.2.0.json` for 1.2.0).
Per code it gives `severity`, `area`, the English ICU MessageFormat `message`
template, the `hint` template or `null`, a one-line `explanation`, and `args`:
per argument its `kind` — `identifier`, `type`, `keyword`, `number`, `vocab`
(an English word or phrase the transpiler picks from the closed set listed in
`values`, which an application may translate) or `text` (open English text the
transpiler builds, such as a nested reason; shown as is). The templates use
simple `{name}` arguments and ICU apostrophe quoting (`''` is one apostrophe,
`'{'` a literal brace); a string argument renders as is and a number in plain
decimal digits. `render_diagnostic(code, args)` returns the English
`(message, hint)`, which equal the diagnostic's `message` and `hint` byte for
byte; in the glue envelope `message` is the message, plus ` — ` and the hint
when there is one.

A code is never removed or reused, and a changed meaning gets a new code
(`tests/fixtures/diagnostic_codes_pin.json` pins each code's templates). A
text no template renders carries `PF-E0000` / `PF-W0000` with the text in
`args.message` (and `args.hint`); the test suite refuses it. The `message`
text itself is unchanged and stays the English rendering; `runtime.error`
text a strategy authors is not a transpile diagnostic.

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
