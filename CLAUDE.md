# CLAUDE.md — pineforge-codegen

> Project memory for AI coding agents. Keep terse and concrete. When the
> codebase invariants below are wrong, the truth is the test suite —
> update this file alongside any change that would break one of these
> claims.

## REQUIRED before claiming any change is done

Both of the following MUST pass before opening a PR, marking a task
complete, or telling the user the change is ready. Skipping either
because "it's just a small change" is how the once-broken paths pinned
in `test_regression_*` survived for so long.

```bash
# 1. Full pytest suite WITH the engine env var. Without the env var
#    the 237 compile-only tests cleanly skip, which means every
#    codegen change goes unverified at the C++ level. The env var is
#    NOT optional for change verification — it is only optional for a
#    quick "did I break a unit test" sanity loop during development.
#    CRITICAL: Always rebuild the sibling pineforge-engine first if any
#    C++ headers or source changed!
export PINEFORGE_ENGINE_INCLUDE=../pineforge-engine/include
pytest

# Expected at HEAD: 944 passed, 1 skipped, 0 failed.
#                   1 skip is the pre-existing test_parser.py:335.
```

```bash
# 2. Corpus regression sweep. (Subset of step 1 — running step 1 already
#    runs this. Listed separately because it is the load-bearing
#    invariant: every Pine v6 strategy in the engine's parity corpus
#    must transpile + compile against the engine headers.)
pytest tests/test_compile_corpus.py

# Expected at HEAD: 206 passed in ~47 s
#   (basic 9 + community 11 + validation 147 + 16 validation_* sub-buckets
#    + parity-anomalies 2).
```

If either check newly fails, fix it before doing anything else. Adding
a strategy to `KNOWN_TRANSPILE_FAILURES` / `KNOWN_COMPILE_FAILURES` to
silence a corpus failure is only acceptable when the failure represents
an intentional, documented support drop — and even then, only with a
matching one-line rationale next to the entry.

The bare `pytest` (no env var) command remains useful during
development for a sub-second feedback loop on the transpiler-only path,
but it is **not sufficient** to validate a change. Always finish with
the env-var run.

## What this is

PineScript v6 → C++ transpiler that emits source linking against the
`pineforge-engine` runtime (`<pineforge/engine.hpp>`, `<pineforge/ta.hpp>`,
…). Public entry point is `pineforge_codegen.transpile(pine_source) -> str`.

This is the **source-available** half of the PineForge stack (PolyForm
Noncommercial — see `LICENSE`). The runtime half (`pineforge-engine`,
Apache-2.0) lives in a sibling repo and is typically checked out at
`../pineforge-engine`. Versions are aligned:
`pineforge-codegen 0.X.Y` requires `pineforge-engine` at the matching ABI
tag — see the version table in `README.md`.

## Pipeline

`transpile()` runs five passes in this order; respect the order when
adding work:

1. `pragmas.extract_pf_trace_pragmas` — pulls `// @pf-trace name=expr`
  comments out before the lexer strips comments.
2. `Lexer` → `Parser` → `Program` AST.
3. `support_checker.check_support_or_raise` — rejects any Pine surface
  PineForge cannot faithfully execute (see "Support contracts" below).
4. `Analyzer.analyze()` → `AnalyzerContext` (type inference, scope
  resolution, per-call-site TA bookkeeping, security registration).
5. `CodeGen(ctx).generate()` → C++ source string.

Pragmas are reattached to `ctx.pf_trace_pragmas` between (4) and (5)
because the analyzer never inspects them; codegen emits the trace tail
in `on_bar`.

## File map

```
pineforge_codegen/
├── lexer.py / tokens.py            Token stream
├── parser.py / ast_nodes.py        Pine v6 AST
├── pragmas.py                      // @pf-trace extraction
├── signatures.py                   Pine v6 builtin signature registry
│                                   (TA / math / str / strategy / input /
│                                    map / built-ins) — single source of
│                                    truth for kwargs + return types.
├── support_checker.py              SUPPORTED_* whitelists + HARD_REJECT
│                                   tables; raises CompileError for any
│                                   construct codegen would otherwise
│                                   silently miscompile.
├── symbols.py                      PineType / TypeSpec / Symbol / Scope
│                                   / SymbolTable
├── tv_input_choices.py             input.string options metadata
├── errors.py                       CompileError + SourceLocation +
│                                   Diagnostic + Level / Phase
├── analyzer/
│   ├── base.py        (~1.4k loc)  Analyzer class — workhorse.
│   ├── call_handlers.py            Per-call-namespace lowering helpers.
│   ├── contracts.py                AnalyzerContext + sub-info dataclasses.
│   ├── tables.py                   TA_CLASS_MAP, TA_PERIOD_ARG,
│   │                               TA_TUPLE_RETURNS, TA_MULTI_CTOR,
│   │                               TA_NO_CTOR, BUILTIN_VARS, BAR_FIELDS,
│   │                               SKIP_FUNCS.
│   ├── types.py                    Type-inference utilities.
│   └── diagnostics.py              Analyzer warning/error emission.
└── codegen/
    ├── base.py        (~1.2k loc)  CodeGen class — class-member layout,
    │                               include set, _matrix_vars / _array_vars
    │                               / _map_vars detection.
    ├── emit_top.py                 #include block, extern "C" wrappers,
    │                               strategy_create / run_backtest_full /
    │                               strategy_set_input layouts.
    ├── visit_stmt.py               Statement-level visitors (var decl,
    │                               assign, if/for/while/switch).
    ├── visit_expr.py               Expression-level visitors (literals,
    │                               operators, ternary, member access).
    ├── visit_call.py   (~1.1k loc) Function-call dispatch — by far the
    │                               most heavily-special-cased file.
    ├── ta.py                       TA call-site allocation + compute()
    │                               vs recompute() emission.
    ├── security.py    (~1.5k loc)  request.security / _lower_tf plumbing.
    ├── tables.py       (~500 loc)  Static dispatch tables: BAR_BUILTINS,
    │                               TA_*, ARRAY_METHODS, MAP_METHODS,
    │                               MATRIX_METHODS, MATRIX_RETURNING_METHODS,
    │                               MATH_FUNC_MAP, STR_FUNC_MAP, …
    ├── types.py                    Codegen-side type inference
    │                               (_infer_type returns a C++ type STRING
    │                               like "std::string" / "double" / "int" /
    │                               "PineMatrix"). Used to gate emission
    │                               choices that the analyzer's PineType
    │                               enum is too coarse for.
    ├── input.py                    input.* / `input()` lowering.
    └── helpers.py                  CPP_RESERVED + small text helpers.
tests/
├── _compile.py                     Helper that runs `g++ -fsyntax-only`
│                                   against the engine headers; reads
│                                   PINEFORGE_ENGINE_INCLUDE +
│                                   PINEFORGE_EIGEN_INCLUDE + CXX env vars.
│                                   Cleanly skips when env is missing.
├── test_compile_smoke.py           Hand-picked Pine snippets that hit
│                                   every dispatch lane; +3 regression
│                                   tests for once-broken paths
│                                   (year(time), matrix-returning methods,
│                                    str.format double-wrap).
├── test_compile_corpus.py          Parametrized over every
│                                   corpus/<bucket>/<strategy>/strategy.pine
│                                   from a sibling pineforge-engine
│                                   checkout (206 strategies, ~47 s).
├── test_official_surface.py        Locks SUPPORTED_* and signatures.* to
│                                   the Pine v6 official inventory
│                                   (sourced from user-pinescript-docs MCP).
├── test_ta_official_surface.py     Same idea, ta.*-specific (predates
│                                   the cross-namespace file).
├── test_support_checker.py         Per-rule support-checker behaviour.
├── test_codegen_new.py     (~1k loc)  Substring assertions on emitted C++.
├── test_signatures.py              Signature-registry unit tests.
└── test_{lexer,parser,analyzer,symbols,errors,…}.py
```

## Architectural invariants — do not break

1. **Versioned in lockstep with the engine ABI.** Bumping
  `pyproject.toml::version` requires a matching `pineforge-engine` tag
   that exposes the C ABI shape we emit against. The transpiler does NOT
   include any runtime artifact at install time — the consumer links
   `libpineforge.a` themselves.
2. **Pure-Python, zero runtime deps.** `pyproject.toml::dependencies = []`.
  Do not introduce a runtime dependency. `dev` extras are pytest only.
3. **Tests never invoke a C++ compiler by default.** The compile-only
  test harness (`tests/_compile.py`) is opt-in via
   `PINEFORGE_ENGINE_INCLUDE`. Without that env var, every compile test
   skips with a message naming the missing knob. Don't make compile
   testing mandatory in CI without first plumbing the engine into CI.
4. `**SUPPORTED_*` whitelists must equal the Pine v6 official inventory**
  (modulo the `KNOWN_*_OMISSIONS` exception sets in
   `tests/test_official_surface.py`). Adding a Pine surface item to
   codegen requires adding it to the OFFICIAL_* set in the test file
   AND removing it from any KNOWN_*_OMISSIONS set; new omissions need a
   one-line rationale right next to the constant.
5. **Every corpus strategy must transpile + compile.**
  `test_compile_corpus.py` parametrizes over all 206
   `corpus/*/*/strategy.pine` files. Per the "REQUIRED before claiming
   any change is done" block at the top of this file, this is a
   mandatory check on every change — not just changes to `analyzer/` or
   `codegen/` — because parser, lexer, support-checker, and signature
   tweaks can also break corpus strategies via second-order effects.
   If you intentionally drop a Pine construct, add the strategy to
   `KNOWN_TRANSPILE_FAILURES` / `KNOWN_COMPILE_FAILURES` with a
   one-line rationale.

## Support contracts

`pineforge_codegen.support_checker` is the gate. Its job is to fail
loudly **before** codegen so users never get a silently miscompiled
strategy. The taxonomy:


| Bucket                           | What                                                                 |
| -------------------------------- | -------------------------------------------------------------------- |
| `HARD_REJECT_FUNC`               | Calls with no PineForge semantics at all (e.g. `request.financial`). |
| `HARD_REJECT_NAMESPACE`          | Whole-namespace rejects — currently EMPTY (the old `ticker.*` blanket reject became per-function `HARD_REJECT_FUNC` entries: `ticker.{renko,kagi,linebreak,pointfigure,new,modify}`; `ticker.inherit`/`ticker.standard` pass through, `ticker.heikinashi` allowed for the chart's own symbol). |
| `DIVERGENT_VARS` (warning) / `DIVERGENT_VARS_ERROR` (reject) | Built-in variables whose value can diverge from TV. `bar_index` and `last_bar_index` warn because they depend on the fed data window; `last_bar_index` lowers to the window's true final index. The ERROR subset is reserved for silent mis-aliases and is currently empty. |
| `BARSTATE_APPROX_VARS` (warning) | Barstate flags PineForge approximates in batch mode.                 |
| `STRATEGY_UNSUPPORTED_PARAMS`    | Per-strategy.* call kwargs that codegen drops silently.              |
| `NOT_YET_FUNC`                   | Implementable but currently no codegen — reject loudly.              |
| `SUPPORTED_*` frozensets         | Per-namespace whitelist of names codegen knows how to emit.          |
| `varip` VarDecl check            | `varip` declarations rejected outright — batch backtests have no realtime tick state. |
| TF literal validation            | `request.security` / `request.security_lower_tf` `timeframe` string literals validated against Pine v6 format at parse time. |
| syminfo na-gap warning           | `SUPPORTED_SYMINFO` = every `SYMINFO_MEMBER_MAP` key, but members whose emission is `na<T>()` or a `get_syminfo_metadata(...)` lookup (root/pricescale/minmove/mincontract/current_contract/expiration_date/isin/sector/industry + fundamentals/recommendations/target_price_*) form `_SYMINFO_SILENT_GAP_FIELDS` (derived from the emission table, so new na-accept fields can't drift out): every read WARNS that the value is na until a data feed injects it. |


When extending codegen with a new Pine builtin: add to the corresponding
`SUPPORTED_*` set (or hard-reject) in `support_checker.py`, register its
signature in `signatures.py`, then wire the dispatch in
`analyzer/call_handlers.py` and `codegen/visit_call.py` (or the
namespace-specific visitor module). The cross-namespace official-surface
test will fail at PR time if you forget any of these steps.

## Known codegen quirks (read before changing)

These bit us once and are now pinned by `test_compile_smoke.py`'s
`test_regression_*` cases. Treat the regression tests as canaries — if
you delete or weaken the special case, the test will tell you.

1. **`year(time)` / `month(time)` / `dayofmonth` / `dayofweek` /
  `hour(time, tz)` / `minute` / `second` / `weekofyear`** — BOTH the
   function-call form and the bare variable form lower to the engine's
   cached, timezone-aware `pine_<field>(ts_ms, tz)` helpers
   (`session_time.hpp`): the variable form via `BAR_BUILTINS`
   (`pine_year(current_bar_.timestamp, syminfo_.timezone)` …), the
   function form via `visit_call.py` (`pine_hour((int64_t)(ts), tz)`),
   so the two forms agree. The two-arg form uses the explicit tz; the
   one-arg form defaults to `syminfo_.timezone` (engine
   `SymInfo::timezone`, "UTC" by default). The old inline
   `setenv("TZ")+localtime_r` lambda (`tz_time_field_lambda`,
   codegen/tables.py) is no longer emitted — per-call tzset churn
   caused a macOS notifyd IPC storm (KI-35) — and has zero call sites.
   Chart display TZ is a separate engine slot (`chart_timezone_`,
   `strategy_set_chart_timezone`) intentionally NOT consulted by these
   builtins; harnesses validating metrics against TV exports for
   non-UTC charts must still set chart_tz to match the chart's TZ at
   export time.
2. **Matrix-returning methods** (`inv` / `pinv` / `transpose` / `copy` /
  `submatrix` / `concat` / `diff` / `mult` / `pow` / `eigenvectors` /
   `kron`) live in `MATRIX_RETURNING_METHODS` (`codegen/tables.py`).
   Both `_register_global_aggregate_member_types` (codegen/base.py) and
   `_visit_var_decl` (codegen/visit_stmt.py) consult this set to declare
   the LHS as `PineMatrix` instead of the analyzer's default `double`.
   Methods returning primitives (`det`, `rank`, `trace`, …) or arrays
   (`row`, `col`, `eigenvalues`) must NOT be in the set.
3. `**str.format(fmt, ...)`** uses `_infer_type` (codegen/types.py) to
  decide whether to wrap each arg in `std::to_string`. Source-text
   prefix heuristics (`"`, `std::string`, `pine_str`) are NOT
   sufficient; bare identifiers and bound results lose their
   string-ness. Booleans go through a TV-style ternary
   (`(v ? "true" : "false")`) so backtest logs match TradingView.
4. **Per-call-site TA cloning.** Multiple `ta.sma(close, ...)` call
  sites need separate `ta::SMA` instances (one per call site),
   addressed via `_cs0`, `_cs1`, … suffixes. The analyzer assigns
   call-site indices in `ctx.func_call_cs_map`; the codegen's
   `_active_call_site_idx` machinery threads them through user-defined
   functions. When adding TA dispatch, make sure the new path respects
   `cs_info` / `_func_cs_var_remap`.
5. `**Series<T>` ring buffer.** Bar-related fields (`close`, `high`,
  `low`, `open`, `volume`, derived `hl2/hlc3/ohlc4/hlcc4`) auto-promote
   to `_s_<name>` series whenever the script reads them with `[k]`. The
   analyzer registers them in `ctx.series_bar_fields`; the codegen
   declares `Series<double> _s_close;` etc. and pushes the current bar's
   value at the top of `on_bar`. `pivot_point_levels` always reads the
   PREVIOUS bar's HLC (`_s_high[1]`, `_s_low[1]`, `_s_close[1]`) per
   Pine v6 semantics with `developing=false`.
6. **`request.security` is strict.** Only `symbol`, `timeframe`,
  `expression`, `gaps`, `lookahead`, and `ignore_invalid_symbol` are
   allowed (`ignore_invalid_symbol` is accepted but inert — the symbol is
   always the chart symbol, so no symbol can be invalid). Symbol must
   resolve to the current chart symbol (`syminfo.tickerid` or
   `syminfo.ticker`, incl. first-binding aliases and
   `ticker.inherit/standard/heikinashi(<chart sym>)`). `gaps` and
   `lookahead` must be the literal `barmerge.gaps_*` /
   `barmerge.lookahead_*` member access (codegen does not parse other
   shapes). `barmerge.lookahead_on` is ACCEPTED with a repaint WARNING —
   engine-supported (first-intrabar publication; script-tf publish gating
   for finer-than-chart targets): see `_check_request_security`'s lookahead
   branch and `test_request_security_lookahead_on_kwarg_warns`. The old
   "hard-rejected" wording here was verified stale against the code on
   2026-07-07 — do not restore it. The `timeframe` argument, when a string
   literal, is validated against the Pine v6 TF format at parse time (PR #3).
7. `**SUPPORTED_LOG`** gates `log.{info,warning,error}`. Without it,
  typos like `log.foo("x")` previously emitted a dead empty-string
   statement. Don't remove the gate.
8. `**CLOSED_TRADE_ACCESSOR_METHODS` vs `OPEN_TRADE_ACCESSOR_METHODS`**
  are intentionally asymmetric. `opentrades` has no `exit_*` fields
   in Pine v6; both lack `direction`. `TRADE_ACCESSOR_METHODS` is kept
   as the union for back-compat but new code should prefer the side-
   specific constant.

## How to add a new Pine v6 function

Worked example: adding hypothetical `ta.foo(source, length)`.

1. **Signature** — `signatures.py`:
  ```python
   _ta("foo", _sig([("source", F), ("length", I)]))
  ```
2. **Analyzer dispatch** — `analyzer/tables.py`:
  ```python
   TA_CLASS_MAP["foo"] = "ta::Foo"
   TA_PERIOD_ARG["foo"] = 1            # length-arg index
  ```
3. **Codegen dispatch** — `codegen/tables.py`:
  ```python
   TA_COMPUTE_ARGS["foo"] = [0]         # which positional args go to .compute()
  ```
4. **Support whitelist** — `support_checker.py`:
  `SUPPORTED_TA` is derived from `TA_CLASS_MAP` automatically; nothing
   to do.
5. **Surface lock** — `tests/test_official_surface.py`:
  add `"foo"` to `OFFICIAL_TA` (if you skipped this step, the test
   still passes for `ta.`* because it's in `test_ta_official_surface.py`
   — keep both files in sync).
6. **Smoke** — `tests/test_ta_official_surface.py::TA_SMOKE_CASES`:
  add `"foo": ("x = ta.foo(close, 5)", "ta::Foo")`.
7. **Engine** — `ta::Foo` class must exist in
  `pineforge-engine/include/pineforge/ta.hpp` with both `compute()`
   and `recompute()` methods. If it doesn't, the addition belongs in
   the engine repo first.
8. Run `pytest` (passes without engine env) and
  `PINEFORGE_ENGINE_INCLUDE=… pytest` (passes with the engine
   compile sweep) before opening a PR.

## How to run tests

See "REQUIRED before claiming any change is done" at the top of this
file for the mandatory verification path. Recap:

```bash
# Quick dev loop — pure transpiler, zero native deps. < 1 s.
# Use during development for fast iteration. NOT sufficient to claim
# a change is done — the 237 compile tests skip in this mode.
pytest

# REQUIRED before claiming any change is done. ~55 s.
export PINEFORGE_ENGINE_INCLUDE=/path/to/pineforge-engine/include
pytest

# Subset shortcut — corpus sweep alone (~47 s) when iterating on a
# change that you suspect specifically affects corpus coverage.
pytest tests/test_compile_corpus.py
```

Expected counts at HEAD:


| Mode                                                    | passed | skipped | failed |
| ------------------------------------------------------- | ------ | ------- | ------ |
| With sibling engine auto-detected (or env var set)      | 944    | 1       | **0**  |
| Without engine (no sibling, no `PINEFORGE_ENGINE_INCLUDE`) | varies | 237+  | **0**  |

The 1 skip is `test_parser.py:335` (empty parameter set, pre-existing,
unrelated). When no engine include is resolvable, the 237 compile-only
tests (31 smoke + 206 corpus) skip cleanly. Auto-detection: `tests/_compile.py`
walks up to 8 directory levels looking for a `pineforge-engine/include` sibling
— no env var needed when the engine repo is checked out at `../pineforge-engine`.

## Conventions

- **Type system.** `PineType` (in `symbols.py`) is intentionally small
and aligns with TradingView's primitive types. For collection /
composite types use `TypeSpec`. Codegen's `_infer_type` returns a C++
type STRING (e.g. `"std::string"`, `"PineMatrix"`) — that's what most
emission paths consume.
- **Errors.** Use `errors.CompileError` for fatal issues raised from
the transpiler. Carry `SourceLocation` so users can map back to the
Pine line/col. Diagnostics inside the support checker use
`Level.WARNING` for divergences-but-not-broken, `Level.ERROR`
otherwise.
- **Comments in emitted C++.** When emitting a fallback / unsupported
stub, include a `/* unsupported: ... */` marker in the source so a
later compile error has context. Avoid emitting bare empty literals.
- **Helper underscores.** Codegen-internal helpers in `codegen/tables.py`
are underscore-prefixed (`_matrix_add_row`, `_merge_kwargs`); they
are not part of the package's external surface.
- **Reserved names.** `codegen/helpers.py::CPP_RESERVED` carries the C++
keyword set; `_safe_name` rewrites Pine identifiers that collide.

## Safety rules for AI agents working in this repo

- **Never silently widen `SUPPORTED_*`.** Every addition needs a
matching entry in the per-namespace official set in
`tests/test_official_surface.py`, or it breaks the surface lock-in.
- **Never delete a `test_regression_*` case** without first
understanding which once-broken codepath it pins. The xfail->pass
history is intentional.
- **Always finish with `PINEFORGE_ENGINE_INCLUDE=... pytest`.** See the
"REQUIRED before claiming any change is done" block at the top.
A diff that passes the pure-transpiler tests but fails on 1 / 206
corpus strategies (or 1 / 31 compile smokes) is still a regression.
Don't report a change as done until the 944-pass run is green.
- **Don't update the version in `pyproject.toml`** without confirming
the engine ABI tag listed in the README's version table actually
exists upstream and exposes the symbols we emit.
- **Don't introduce runtime dependencies.** Pure-Python is the install
contract. Test extras (pytest) are the only allowed `[project.optional-dependencies]`.

