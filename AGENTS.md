# AGENTS.md — pineforge-codegen

> Project memory for AI coding agents. Keep terse and concrete. When the
> codebase invariants below are wrong, the truth is the test suite —
> update this file alongside any change that would break one of these
> claims.
>
> This file is the harness-neutral twin of `CLAUDE.md` (Codex, OpenCode and
> other agents read it): keep the two in step. Only the last section, the
> parity campaign gate that a harness without Claude Code's hooks must run by
> hand, is this file's own.

## REQUIRED before claiming any change is done

Both of the following MUST pass before opening a PR, marking a task
complete, or telling the user the change is ready. Skipping either
because "it's just a small change" is how the once-broken paths pinned
in `test_regression_*` survived for so long.

```bash
# 1. Full pytest suite WITH the paired engine headers, Eigen, generated
#    version header and built runtime. Missing native inputs cause tests
#    to skip, which leaves C++ behavior unverified. Explicit paths make
#    coverage reproducible even if a sibling checkout is auto-detected.
#    CRITICAL: Always rebuild the sibling pineforge-engine first if any
#    C++ headers or source changed!
export PINEFORGE_ENGINE_INCLUDE=../pineforge-engine/include
# Point Eigen at a system include tree or CMake's fetched source:
export PINEFORGE_EIGEN_INCLUDE=../pineforge-engine/build/_deps/eigen-src
# The generated version header, built runtime and corpus unlock their tests:
export PINEFORGE_GENERATED_INCLUDE=../pineforge-engine/build/include
export PINEFORGE_ENGINE_LIB=../pineforge-engine/build/lib/libpineforge.a
export PINEFORGE_ENGINE_CORPUS=../pineforge-engine/corpus
python -m pytest -ra
# Read the result and skip reasons; use --collect-only -q for today's count.
```

```bash
# 2. Corpus regression sweep. (Subset of step 1 — running step 1 already
#    runs this. Listed separately because it is the load-bearing
#    invariant: every Pine v6 strategy in the engine's parity corpus
#    must transpile + compile against the engine headers.)
python -m pytest -ra tests/test_compile_corpus.py
# Confirm that collection found the public corpus and no compile test skipped.
```

If either check newly fails, fix it before doing anything else. Adding
a strategy to `KNOWN_TRANSPILE_FAILURES` / `KNOWN_COMPILE_FAILURES` to
silence a corpus failure is only acceptable when the failure represents
an intentional, documented support drop — and even then, only with a
matching one-line rationale next to the entry.

A quick pytest run without a resolvable engine remains useful during
development, but it is **not sufficient** to validate a change. Always
finish with the engine-enabled run and inspect its skip reasons.

## What this is

PineScript v6 → C++ transpiler that emits source linking against the
`pineforge-engine` runtime (`<pineforge/engine.hpp>`, `<pineforge/ta.hpp>`,
…). Public Python entry points are `pineforge_codegen.transpile()` and
`pineforge_codegen.transpile_full()`; the Pyodide package ships the
`gate/glue.py` JSON protocol. See `docs/PUBLIC_CONTRACT.md`.

This is the **source-available** half of the PineForge stack (PolyForm
Noncommercial — see `LICENSE`). The runtime half (`pineforge-engine`,
Apache-2.0) lives in a sibling repo and is typically checked out at
`../pineforge-engine`. A released codegen `X.Y.Z` pairs only with engine
`vX.Y.Z`; prereleases match exactly. Use that release's generated headers and
static library, and regenerate C++ and relink on every pair change. Equal
`PF_ABI_VERSION` values are insufficient. Engine main currently uses the
`engine_script_run_v19` C++ namespace; see `README.md` and `CONTRIBUTING.md`.

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
├── limits.py                      Located source/complexity/time budgets
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
├── pine_spelling.py                String-literal-safe helpers for the Pine
│                                   spellings of TA ctor args (inline
│                                   input calls kept whole; see quirk 10)
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
    ├── tv_number_format.py         TU-local numeric formatter emitted for
    │                               str.tostring / str.format / log.*.
    ├── input.py                    input.* / `input()` lowering.
    └── helpers.py                  CPP_RESERVED + small text helpers.
tests/
├── _compile.py                     Helper that runs `g++ -fsyntax-only`
│                                   against the engine headers; reads
│                                   PINEFORGE_ENGINE_INCLUDE +
│                                   PINEFORGE_EIGEN_INCLUDE + CXX env vars.
│                                   Cleanly skips when env is missing.
│                                   STRATEGY_FP_FLAGS (-ffp-contract=off,
│                                   as the engine builds) goes on every
│                                   compile that emits a strategy TU.
├── _e2e.py                         Shared harness of the test_e2e_*.py
│                                   modules: transpile through gate/glue.py
│                                   transpile_json, build the TU against the
│                                   built runtime, run the engine's
│                                   run_strategy.py on the corpus 15m feed
│                                   (inputs.json overrides, @pf-trace);
│                                   reference_codegen() transpiles with
│                                   another commit's codegen (git archive).
├── test_compile_smoke.py           Hand-picked Pine snippets that hit
│                                   every dispatch lane; +3 regression
│                                   tests for once-broken paths
│                                   (year(time), matrix-returning methods,
│                                    str.format double-wrap).
├── test_compile_corpus.py          Parametrized over every
│                                   corpus/<bucket>/<strategy>/strategy.pine
│                                   from a sibling pineforge-engine
│                                   checkout; collection size follows
│                                   the checked-out public corpus.
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

1. **Exact engine release pairing.** Bumping `VERSION` requires the
   matching `pineforge-engine` tag (`X.Y.Z` with `vX.Y.Z`; prereleases exactly).
   Use that tag's generated headers and `libpineforge.a`; regenerate every
   strategy TU and relink on a pair change. `PF_ABI_VERSION` equality alone is
   insufficient. The transpiler does not ship a runtime artifact.
2. **Pure-Python, zero runtime deps.** `pyproject.toml::dependencies = []`.
  Do not introduce a runtime dependency. `dev` extras are pytest only.
3. **Compile tests need an engine checkout and Eigen.** The harness
   (`tests/_compile.py`) uses `PINEFORGE_ENGINE_INCLUDE` or a resolvable sibling
   checkout. It skips when the engine or Eigen is absent; runtime tests also
   need a built `libpineforge.a`. Do not make compile testing mandatory in CI
   without first plumbing the paired engine into CI.
4. `**SUPPORTED_*` whitelists must equal the Pine v6 official inventory**
  (modulo the `KNOWN_*_OMISSIONS` exception sets in
   `tests/test_official_surface.py`). Adding a Pine surface item to
   codegen requires adding it to the OFFICIAL_* set in the test file
   AND removing it from any KNOWN_*_OMISSIONS set; new omissions need a
   one-line rationale right next to the constant.
5. **Every corpus strategy must transpile + compile.**
  `test_compile_corpus.py` parametrizes over every
   `corpus/*/*/strategy.pine` file in the checked-out public corpus.
   Per the "REQUIRED before claiming any change is done" block at the top
   of this file, this is a
   mandatory check on every change — not just changes to `analyzer/` or
   `codegen/` — because parser, lexer, support-checker, and signature
   tweaks can also break corpus strategies via second-order effects.
   If you intentionally drop a Pine construct, add the strategy to
   `KNOWN_TRANSPILE_FAILURES` / `KNOWN_COMPILE_FAILURES` with a
   one-line rationale.

## Support contracts

`pineforge_codegen.support_checker` is the gate. Its job is to fail
loudly **before** codegen so users never get a silently miscompiled
strategy. An error raises `CompileError` (carrying every diagnostic); the
warnings of a script that transpiles (support checker, then analyzer and
codegen `ctx.diagnostics` -- the codegen appends through `_codegen_warning`)
come back as `transpile_full(...)["diagnostics"]` and in `transpile_json`'s
success envelope under `diagnostics`, in the error envelope's entry format.

A change must never make a script that transpiles and compiles today fail
to transpile: when the engine cannot express what such a script asks for, it
keeps its current lowering and gets a WARNING naming the approximation and
the missing engine capability. Refuse only a spelling TradingView itself
rejects, or one that already failed the C++ compile (supervisor rule, since
C4 W8 lost 15 excellent campaign probes to a refusal of a working shape). The
taxonomy:


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
| `ta.vwap` anchor                 | Omitted-anchor VWAP and its band form keep `ta::VWAP` / `ta::VWAPBands` exactly. Every explicit scalar or band anchor, including `timeframe.change("1D")` / `("D")`, is passed per bar to the TA1 anchored VWAP shim; a TU built against an older engine falls back to the historical session-day lowering. TradingView tapes for both a mid-session and a day-boundary start show the explicit daily form is `na` until the first day change, while omitted-anchor VWAP is finite from bar 0. |
| Input titles (codegen)           | `_check_input_titles` refuses a title that is not a compile-time string constant (TradingView: `title (const string)`) before generation; PineForge keys every override by the title. |
| Input keys (codegen)             | `_check_input_keys` WARNS, once per override key that several inputs share (the title, else the name of the declaration holding the call: two untitled calls in one declaration, two outside any declaration, an untitled call and another titled with its name, a title repeated across `group=`s), naming every input the key reaches: one override sets all of them. TradingView tells such inputs apart, so the script transpiles unchanged (closed strategies 125-cleightyp and 162-nicocashfx repeat titles across groups and grade excellent at their defaults). |
| Parser syntax                    | A `ParseError` at top level or inside a block raises a located `CompileError` instead of discarding tokens. Adjacent expressions on one line (`x = 1 2`, `s = "a" "b"`) are rejected at the second token. The 312 validation probes, 602 corpus `.pine` files and 101 closed strategies had zero parser recovery events at the 2026-09-24 census; no validated script lost support. |
| Bare `ta.tr`                    | The variable equals `ta.tr(false)`: it is `na` when the previous close is `na`, including bar 0. The explicit `ta.tr(true)` and `ta.atr` paths retain their own first-bar behavior. TradingView's 2025-04-01 `c6-ta-tr-bar-zero` trade booked Signal `bare-na`; `tests/test_e2e_bare_ta_tr.py` compares the emitted bar traces and trades with `ta.tr(false)`. |
| `ta.*` call arguments            | `_check_ta_arguments` binds every `ta.*` call to TradingView's signature (`signatures.TA_FUNCTIONS`: TradingView's parameter names, e.g. `series` for `ta.alma` / `bb` / `bbw` / `cmo` / `kc` / `kcw`, and required/optional split) and refuses what TradingView rejects: an unknown keyword, an argument too many, one given twice or a missing required one -- each used to be dropped, shifted into the next constructor slot or passed to a `compute()` that does not exist. `ta.vwap()` with no argument reads as the bare property, as it always has. |
| `ta.*` TA1 arguments | The TA1 engine computes `ta.alma` `floor`, `ta.kc` / `ta.kcw` `useTrueRange`, explicit `ta.vwap` anchors, and `ta.pivot_point_levels` `anchor` / `developing` exactly. The generated TU selects those APIs with `PF_ALMA_HAS_FLOOR`, `PF_KC_HAS_USE_TRUE_RANGE`, `PF_VWAP_HAS_ANCHOR_INPUT`, and `PF_PIVOT_LEVELS_HAS_ANCHOR`; when a macro is absent, the shim falls back to the historical lowering. Only omitted-anchor VWAP keeps `ta::VWAP` / `ta::VWAPBands`; every explicit anchor, including `timeframe.change("1D"|"D")`, uses `ta::AnchoredVWAP` / `ta::AnchoredVWAPBands`. The pivot free function remains only for literal/aliased `anchor=true, developing=false`; every other spelling uses `ta::PivotPointLevels`. |
| Chart EMA warmup | The generated `_PFKC` / `_PFKCW` shims scope the engine's EMA warmup selector around their internal EMA basis. KC/KCW middle is `na` until the Pine length is warm, including bar 0, without changing unrelated chart EMA call sites. The TradingView probe in `tests/test_kc_middle_band.py` pins KC middle against an independent EMA at bar 0 and the first finite bar. |
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
3. **`str.format` / `str.tostring` / `log.*` number text.** The emitted
   helper in `codegen/tv_number_format.py` takes typed format arguments and
   renders TradingView's `#,###.###` default for `str.format`, its
   `{0,number,...}` styles and apostrophe quoting, and `str.tostring`'s
   `#.##########` default, custom `#`/`0`/`%` patterns, percent and volume.
   `format.mintick` delegates to the engine's tick-rounding formatter.
   Numeric rounding converts a double to its shortest round-trip decimal with
   C++17 `std::to_chars`, shifts the decimal point as digits, then rounds the
   first discarded digit half-up; it uses no floating-point intermediate or
   `long double`. `tests/test_tv_number_format_rounding.py` pins 42 edge
   cases on AppleClang arm64 and GCC 13 Linux amd64.
   The lowering is `visit_call._str_format_expr`, shared with
   `log.info` / `log.warning` / `log.error(fmt, arg0, ...)` (TradingView:
   the second overloads of `log.*()` have "the same parameter signature and
   formatting behaviors as str.format()"; the arguments used to be dropped);
   `log.*(message)` logs its message as is. The engine's `str_format` only
   substitutes plain `{i}`; its `str_tostring` uses six fixed decimals by
   default, multiplies `format.percent` by 100, and retains two decimals for
   every volume. The codegen helper covers those gaps without an engine ABI
   change. `tests/test_e2e_tv_number_rendering.py` pins 28 TradingView order
   Signals, while `tests/test_e2e_log_format.py` pins dynamic log lines.
   `signatures.py` names the first `str.format` parameter `formatString`,
   and the call visitor binds `str.format(formatString="...")`.
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
   value at the top of `on_bar`. `pivot_point_levels` reads the
   PREVIOUS bar's HLC (`_s_high[1]`, `_s_low[1]`, `_s_close[1]`) on the
   free-function `anchor=true, developing=false` route. Other forms use the
   TA1 anchored-period class.
6. **`request.security` is strict.** Only `symbol`, `timeframe`,
  `expression`, `gaps`, `lookahead`, and `ignore_invalid_symbol` are
   allowed (`ignore_invalid_symbol` is accepted but inert — the symbol is
   always the chart symbol, so no symbol can be invalid). An unconditional
   alternate symbol is rejected. Chart-symbol aliases resolve by lexical
   binding, so a local rebind cannot taint an unrelated global. A ternary
   symbol warns if either arm can select an alternate feed; its existing
   chart-symbol lowering stays accepted to preserve working scripts. Safe
   forms include `syminfo.tickerid`, `syminfo.ticker`, and
   `ticker.inherit/standard/heikinashi(<chart sym>)`. `gaps` and
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
9. **No implicit `double` -> integer narrowing, ever.** A `double`
  expression reaching an `int` / `int64_t` slot must go through
   `helpers.na_preserving_int_cast` (`is_na(_pf_v) ? na<int>() :
   (int)_pf_v`), applied by `types._coerce_int_slot`. An *implicit*
   narrowing of a NaN is undefined ([conv.fpint]) and the compilers
   disagree: AppleClang arm64 and g++ aarch64 give 0 at every `-O`,
   g++ x86-64 gives `INT_MIN` at `-O0`/`-O1` and 0 from `-O2`. The
   engine's contract (`include/pineforge/na.hpp`) is that an integer
   `na` IS `std::numeric_limits<T>::min()`, which is what `is_na(T)`
   tests, so one bench slot booked 2412 trades built at `-O3` and 2411
   (TradingView's count) at `-O1` from the same source. A plain
   `(int)x` cast does NOT fix this — it only silences the warning; the
   `is_na` test is what makes it defined. Where the value is needed is
   decided by `types._emitted_value_is_double`, which is deliberately
   NOT `_infer_type(node) == "double"`: `_infer_type` answers "what
   does this slot hold" (Pine's `int` for `math.round(x)`, whose
   emission is a `double` `std::round(x)`) and falls back to `double`
   for shapes it cannot resolve (integer arithmetic, loop binders,
   colour constants, `bar_index`). Both directions of that mismatch
   are enumerated there; extend it, not `_infer_type`, when adding a
   lowering. The check that keeps the class closed is the compiler:
   `tests/test_na_int_narrowing.py` compiles a per-site battery with
   `-Wfloat-conversion` and requires an empty diagnostic list.
   Numeric values entering a boolean context must use the Pine truthiness
   helper: `na` is false, while C++ would treat both NaN and the integer
   `na` sentinel as true. `types._coerce_bool_expr` covers `if` / `while` /
   ternary / `and` / `or` and user-function or TA bool parameters;
   compile-time literals and already-boolean values are proven non-`na` and
   stay native. `tests/test_na_truthiness.py` pins the generated form and
   runtime rows. The integer narrowing and truthiness batteries together are
   the closed conversion-site check.
10. **An inline `input.*()` call is one leaf of a TA length.** TA ctor
   args (and derived / user-function lengths) reach the codegen as Pine
   source spellings. `pine_spelling.py` keeps an inline input call whole
   there — keyword args included, strings escaped — and the codegen
   masks its argument text (title string, keyword names) out of every
   identifier scan, so `ta.ema(close, input.int(9, "fast"))` gets the
   same reset, placeholder and manifest entry as
   `len = input.int(9, "fast")` + `ta.ema(close, len)` (issue #132; the
   title used to read as an unknown identifier). An inline call is
   admitted exactly when its bound spelling would be input-backed
   (`_is_stable_inline_input`: not a source input, constant defval); a
   length that is otherwise a series stays refused. A generic `input()` is
   the same leaf in a declared length (`len = input(9) + 1`:
   `_expr_is_stable` admits it through `_is_stable_inline_input`;
   `tests/test_e2e_generic_input_length.py`). The input manifest
   lists every global-scope input call, inline ones too, with `title` =
   the key the C++ reads it by. `tests/test_e2e_inline_input_ta_length.py`
   pins it end to end, for every TA constructor.
11. **A lookback that is a `compute()` argument goes to `compute()`.** Most
   `ta::` classes take their length in the constructor and only sources in
   `compute()`. `ta::Change` splits it: the constructor's `max_length`
   only bounds the kept history, `compute(src, length = 1)` reads the
   lookback, so `TA_COMPUTE_ARGS["change"] = [0, 1]` (analyzer) sends the
   length to both — without it every `ta.change(src, n)` was a one-bar
   change. `ta::ValueWhen` is the mirror image: `occurrence` always reached
   `compute()`, but the history bound is the constructor's `max_occurrence`
   (default 1, two values kept), so `TA_PERIOD_ARG["valuewhen"] = 2` sends
   it to the constructor as well (it sat in `TA_NO_CTOR`, and every
   `occurrence >= 2` read `na`). `ta.vwap`'s default anchor keeps the
   historical VWAP route; every other anchor is carried in the anchored
   shim's per-bar compute arguments. `ta.alma`'s `floor` and `ta.kc` /
   `ta.kcw`'s `useTrueRange` are constructor arguments in the TA1 shims.
   Feature macros select the exact TA1 classes and the shim branches preserve
   the old lowering when a macro is absent. The routing table is the
   ANALYZER's `TA_COMPUTE_ARGS` (analyzer/tables.py):
   without an entry every non-constructor argument goes to `compute()`, so a
   new TA whose `compute()` in `ta.hpp` does not take every such Pine
   parameter needs one. The one-arg `ta.highest` / `lowest` / `highestbars`
   / `lowestbars(length)` forms (positional or `length=`) read high / low
   (`TA_LENGTH_ONLY_DEFAULT_SOURCE`). `tests/test_e2e_ta_argument_routing.py`
   compares every routed spelling with a spelled-out reference, every warned
   one with the pre-C5 (e6a64cd) build of the same script, and pins every
   refusal. `tests/test_e2e_ta_change_length_and_input_keys.py`
   pins `ta.change` bar by bar against `src - src[n]`,
   `tests/test_e2e_valuewhen_occurrence.py` `ta.valuewhen` against a
   spelled-out chain, `tests/test_e2e_vwap_anchor.py` `ta.vwap` against a
   spelled-out anchored VWAP.
12. **One key per input, whichever path reads it.** A `get_input_*()` read
   keys the input by `_get_input_title(call, ...)`, the string the manifest
   lists: the title's value (`_input_title_value`: a literal, a `+` of
   constants, or a never-reassigned non-`var` global name bound to one;
   `_check_input_titles` refuses any other title before generation), else
   the name of the declaration holding the call
   (`pine_spelling.input_binding_names`: `v = input.*()`, or a call nested
   anywhere in a declaration's value, `var` or not — TradingView: "If not
   specified, the variable name is used as the input's title"), else `""`.
   The name comes from the call node, not from the caller, so the member,
   the TA reset, a
   request.security timeframe and an alias's read (`b = a`) agree; a call
   re-spelled for a derived length carries it as `title=`, since the
   re-parsed node has no declaration. `_input_key_literal` spells the key
   as a C++ literal (a title holding `"` or `\` used to break the TU). Inputs
   sharing a key cannot be overridden apart, so `_check_input_keys` warns
   (see "Input keys" above; an untitled call nested in a plain declaration
   used to be keyed `""`).
   `tests/test_e2e_untitled_input_keys.py`,
   `tests/test_e2e_nested_input_keys.py` and
   `tests/test_e2e_input_title_constant.py` pin it end to end.
13. **A precalculated TA site is built like the live one.** A static chart
   TA site (bar-data `compute()` arguments) is precalculated:
   `prepare_script_run()` calls `precalculate()` when the engine allows it
   (no magnifier, empty timeframes), and a call nested in an expression
   then reads `_precalc_<member>[bar_index_]` (a direct `x = ta.foo(...)`
   assignment does not).
   `precalculate()` builds each site from `_ta_run_ctor_args`, the same
   override-aware arguments as the `_ta_initialized_` reset, never from
   compile-time values — those are the input's default (or the placeholder
   `1` for a length that does not fold), and an override never reached a
   nested call. `tests/test_e2e_precalc_input_override.py` pins every
   spelling against literal-length twins.
14. **String escapes are the Pine manual's.** The lexer (`_read_quoted`)
   reads `\n` / `\t` as U+000A / U+0009 and `\\`, `\"`, `\'` as the
   character; any other `\X` reads as `X` ("the character's meaning does
   not change": `\T`, `\r`, `\u`, `\x`, `\0`, ...). A value reaches C++
   through `_cpp_string_escape` (escapes `\n`, `\r`, `\t`) and a Pine
   re-spelling through `pine_string_literal` (spells `\n`, `\t`). A
   multiline `"""..."""` / `'''...'''` literal (`_read_multiline`) is
   everything up to the first three unescaped delimiter quotes, line breaks
   as U+000A and indentation kept; a single-line literal that continues on
   an indented line reads the break as one space (the manual's deprecated
   line wrapping); one never closed is refused at its opening quote. A
   `// @pf-trace` line inside a multiline or wrapped string is text, not a
   pragma: the pre-pass uses the Pine lexer to exclude string spans.
   `tests/test_e2e_string_escapes.py`
   and `tests/test_e2e_multiline_strings.py` pin each escape and the
   manual's multiline examples through the manifest, an override and
   per-bar `str.*` traces.
15. **Top-level lazy-edge `ta.*` sites whose history is read are hoisted to
    every-bar evaluation.** TradingView (pinned 2026-09-03 with `lab tv`,
    NYSE:F 1D) advances a stateful `ta.*` call on EVERY bar when it sits below
    a Pine-v6 lazy `and`/`or` RHS or a ternary arm of a top-level statement
    AND the call's own history is referenced (`ta.sma(close, 5)[1]`; the
    bare twins of every tape are per-execution);
    short-circuiting gates only the value, and `[1]` on it is the previous
    BAR. Without a `[k]` read the reached-only inline compute is TV's clock
    (oliver1002 / louislapis9 / ycelestine77 / quantbyboji / miemomo3 exact at
    100% on it, 2026-09-04). For a `[k]`-read site codegen emits
    `const auto _pf_every_bar_ta_N = <site>;` (plus the site's `_hist_call_*`
    push for a direct `[k]`) BEFORE the statement, in dynamic mode too
    (`codegen/ta.py::_lazy_edge_ta_hoist_plan`, `_emit_lazy_edge_ta_hoists`;
    `tests/test_lazy_edge_ta_every_bar.py`). The rule is per family
    (cadence-7 ternary/lazy-and probes, same tapes) and the hoist is an
    ALLOW-LIST (`LAZY_EVERY_BAR_TA` = highest/lowest/sma/ema) gated on the
    `[k]` read: a broad hoist of every family cost 170 tiers / 30 hard lanes
    on Cloud Run (2026-09-04), so an unpinned family keeps its existing
    lowering until a tape pins it. `change`/`mom`/`roc` (`LAZY_SOURCE_CLOCK_TA`) read the
    call's OWN held `source[length]` -- written only when the call executes,
    held on skipped bars, na before the first execution -- through the
    generated `_PFLazySourceClock` + `_pf_lazy_src_hist_N` members
    (`tests/test_lazy_source_clock*.py`; this replaced the #64 roc3-only
    clock, whose eager first-execution fallback the tapes refute; its eager
    chart `source[length]` read between executions closer than `length` bars
    is kept for chart-builtin sources via `_pf_lazy_src_chart_N`);
    `cum`/`barssince`/`valuewhen`/`cross*`/`rising`/`falling`/`math.sum`
    (`LAZY_PER_EXECUTION_TA`) keep the reached-only inline compute, which is
    TradingView's per-execution clock, and never precalc.
    Sites inside `if`/loop/function bodies, `else if` conditions, `var`
    initializers, `request.security` payloads and tuple-returning sites keep
    their existing lowering. The old "lazy SMA/EMA must not precalc" pins
    (pf-probe-oliver-dual-vol-sma) encoded the refuted per-call clock and were
    re-pinned in `test_codegen_validation_fixes.py`.

## How to add a new Pine v6 function

Worked example: adding hypothetical `ta.foo(source, length)`.

1. **Signature** — `signatures.py`, with TradingView's parameter names
  and required/optional split (a default marks an optional parameter): the
  support checker binds every call to it and refuses what does not bind.
  ```python
   _ta("foo", _sig([("source", F), ("length", I)]))
  ```
2. **Analyzer dispatch** — `analyzer/tables.py`:
  ```python
   TA_CLASS_MAP["foo"] = "ta::Foo"
   TA_PERIOD_ARG["foo"] = 1            # length-arg index
  ```
3. **Compute routing** — `analyzer/tables.py` (the table that routes;
  `codegen/tables.py` keeps a descriptive copy):
  ```python
   TA_COMPUTE_ARGS["foo"] = [0]         # which positional args go to .compute()
  ```
  Without an entry every non-constructor argument reaches `.compute()`: add
  one whenever `ta::Foo::compute` does not take every such Pine parameter,
  and refuse (support checker) a parameter value the class cannot compute.
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
# Quick feedback; native tests skip if no engine and Eigen are resolvable.
python -m pytest -ra

# Required release check with the matching engine source and build.
export PINEFORGE_ENGINE_INCLUDE=/path/to/pineforge-engine/include
export PINEFORGE_EIGEN_INCLUDE=/path/to/eigen-headers
export PINEFORGE_GENERATED_INCLUDE=/path/to/engine-build/include
export PINEFORGE_ENGINE_LIB=/path/to/engine-build/lib/libpineforge.a
export PINEFORGE_ENGINE_CORPUS=/path/to/pineforge-engine/corpus
python -m pytest -ra
python -m pytest -ra tests/test_compile_corpus.py

# Ask pytest for the current collection size instead of relying on a fixed count.
python -m pytest --collect-only -q
```

The full suite includes the corpus sweep. The summary and skip reasons are
more important than a frozen pass count: missing engine headers, Eigen,
`libpineforge.a`, or corpus data can leave a green but incomplete run.
`tests/_compile.py` can auto-detect a sibling engine checkout, but explicitly
setting all paths makes the verification reproducible. See `CONTRIBUTING.md`.

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
- **Reserved names.** `codegen/helpers.py::CPP_RESERVED` carries every C++17
and C++20 keyword/operator alternative plus header macros and emitter names
that can collide. `_safe_name` allocates distinct escapes against all authored
spellings; UDT fields use the same mapping.
- **Input limits.** `limits.py` sets 131,072 source characters, 256 tokens or
4,096 characters per logical statement, 32 delimiter/block levels, 64 AST
nodes of depth, 1,024 statements, and a cooperative 30-second guard. Every
limit raises a located `CompileError`. Across the 314 public corpus scripts
and 277 gate fixtures, maxima were 9,869 source characters, 64 tokens / 420
characters per logical statement, depth 3 delimiters / 4 blocks / 10 AST
nodes, and 140 statements (2026-09-25).

## Safety rules for AI agents working in this repo

- **Never silently widen `SUPPORTED_*`.** Every addition needs a
matching entry in the per-namespace official set in
`tests/test_official_surface.py`, or it breaks the surface lock-in.
- **Never delete a `test_regression_*` case** without first
understanding which once-broken codepath it pins. The xfail->pass
history is intentional.
- **Always finish with the full engine-enabled `python -m pytest -ra`**
  and the corpus gate shown at the top. A single corpus or compile-smoke
  failure is a regression; inspect skips to confirm the C++ paths ran.
- **Don't update `VERSION`** without confirming the exact matching engine tag
  and its generated headers and static library are available.
- **Don't introduce runtime dependencies.** Pure-Python is the install
contract. Test extras (pytest) are the only allowed `[project.optional-dependencies]`.

## Parity campaign gate (applies on EVERY harness)

Pushes and PRs from this repo are gated by the PineForge parity campaign: a
fresh (≤6h) PASS verdict must bind the exact (engine, codegen) HEADs, recorded
on the campaign registry. Under Claude Code a PreToolUse hook
(`.claude/settings.json`, calls `pineforge-workflow/campaign/hooks/pr-gate.mjs`)
enforces this on `git push` / `gh pr create|ready|merge`. Codex, OpenCode, and
other harnesses run NO hook — the discipline is exactly as binding there: before
any push, run the gate and record the verdict (see the `pr-gate` skill in
`pineforge-workflow/.claude/skills/` — plain markdown, readable anywhere):

```sh
gcloud run jobs execute pineforge-pr-gate --project gen-lang-client-0864094636 \
  --region asia-east1 --args '^|^--pipeline|pr_gate|--conf|<conf-json>'
lab gate record --verdict <verdict.json> --engine <sha> --codegen <sha>
```

Merged single-axis PRs advance the campaign baseline automatically
(`.github/workflows/promote-baseline.yml`); a squash/rebase that rewrites the
sha defers and must be re-gated.
