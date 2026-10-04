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
`pineforge-engine` runtime (`<pineforge/source/pine_strategy_host.hpp>`,
`<pineforge/ta.hpp>`, …; 0.10.4 emitted `<pineforge/engine.hpp>`). Public
Python entry points are `pineforge_codegen.transpile()` and
`pineforge_codegen.transpile_full()`; the Pyodide package ships the
`gate/glue.py` JSON protocol. See `docs/PUBLIC_CONTRACT.md`.

This is the **source-available** half of the PineForge stack (PolyForm
Noncommercial — see `LICENSE`). The runtime half (`pineforge-engine`,
Apache-2.0) lives in a sibling repo and is typically checked out at
`../pineforge-engine`. From 1.0.0 on, a released codegen `X.Y.Z` pairs only
with engine `vX.Y.Z`; prereleases match exactly. Codegen 1.0.1 pairs with
engine `v1.0.1` (the `pineforge-release` image `1.0.1`), and codegen 1.0.0
with engine `v1.0.0` (the image `1.0.0`). On the 0.x line the versions are
independent: the last 0.x release, codegen 0.10.4, pairs with engine
`v0.13.1` (the `pineforge-release` image `0.1.25`). Use the paired release's
generated headers and static library, and regenerate C++ and relink on every
pair change. Equal `PF_ABI_VERSION` values are insufficient. Engines `v1.0.0`
and `v1.0.1` use the `engine_script_run_v19` C++ namespace; see `README.md`
and `CONTRIBUTING.md`.

## Pipeline

`transpile()` runs five passes in this order; respect the order when
adding work:

1. `pragmas.extract_pf_trace_pragmas` — pulls `// @pf-trace name=expr`
  comments out before the lexer strips comments.
2. `Lexer` → `Parser` → `Program` AST. Before the support check,
  `library_inline.inline_libraries` inlines the libraries the script imports
  and `block_locals.rename_block_locals` names apart the block declarations a
  previous run asked for.
3. `support_checker.check_support_or_raise` — rejects any Pine surface
  PineForge cannot faithfully execute (see "Support contracts" below).
4. `Analyzer.analyze()` → `AnalyzerContext` (type inference, scope
  resolution, per-call-site TA bookkeeping, security registration). Four
  AST passes run before it: `external_requests.lower_no_data_requests`
  (requests the support checker lowered), `security_contexts.
  specialize_security_contexts` (helper request contexts),
  `finite_ta_length`'s bounded extrema lengths and `builtin_keywords`
  (`nz` / `fixnan` keyword arguments in parameter order, the helper copies
  `security_contexts` made included).
5. `CodeGen(ctx).generate()` → C++ source string.

Pragmas are reattached to `ctx.pf_trace_pragmas` between (4) and (5)
because the analyzer never inspects them; codegen emits the trace tail
in `on_bar`.

`_generate` runs (2)-(5) first without the per-call clones of functions that
read a `session.<flag>[k]` (the calls they had before such reads were
supported), then again with the functions whose reads that C++ holds cloned
as well (`session_clones`), until the C++ asks for no more: a clone can make
a caller's read reach the C++. A read in an argument the codegen leaves out
never costs a clone, which along a deep call tree grow with every path.

## File map

```
pineforge_codegen/
├── lexer.py / tokens.py            Token stream
├── parser.py / ast_nodes.py        Pine v6 AST
├── limits.py                      Located source/complexity/time budgets
├── builtin_keywords.py             nz / fixnan keyword arguments rewritten
│                                   to their positions after the support
│                                   check (TradingView's parameter order)
├── pragmas.py                      // @pf-trace extraction
├── library_modules.py              A Pine library source parsed as a
│                                   module: its library() declaration,
│                                   exports, own //@version, imports.
├── pine_libraries.py               Where an imported library's source
│                                   comes from: libraries= or the
│                                   script's own requests manifest
│                                   (env contract), sha-verified.
├── library_inline.py               Inlines the libraries a script
│                                   reaches, before the support check:
│                                   reachability, module-qualified
│                                   names, alias/method resolution.
├── block_locals.py                 A top-level block declaration whose
│                                   type the member of its name cannot
│                                   hold, named apart (a second run).
├── library_v5.py                   v5 rules inside an inlined v5
│                                   library's bodies: V5_RULES gives
│                                   every v5->v6 change a disposition
│                                   (lowered, refused, not applicable).
├── external_requests.py            Requests with no data (other symbols,
│                                   fundamentals): the trade slice that
│                                   lowers a display-only one to na, and
│                                   the run-time missing-data reads of one
│                                   lowered onto pinned data.
├── security_contexts.py            request.security symbol/timeframe
│                                   and the helper parameters a payload
│                                   reads, resolved through helper call
│                                   paths (one context per value) before
│                                   the analyzer; refuses what
│                                   registration cannot compute.
├── request_discovery.py            transpile_full()'s requests: the
│                                   other symbols' feeds a script reads,
│                                   keyed as registration computes them
│                                   (after generation; the C++ unchanged).
├── session_reads.py                The session.<flag>[k] reads the generated
│                                   C++ holds (analyzer and codegen share it).
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
├── finite_ta_length.py             Finite-choice ta.highest / ta.lowest
│                                   lengths: one fixed extrema history per
│                                   choice, selected per bar.
├── method_binding.py               Typed Pine methods inventoried before
│                                   analysis, bound in source order.
├── analyzer/
│   ├── base.py        (~6.9k loc)  Analyzer class — workhorse.
│   ├── call_handlers.py            Per-call-namespace lowering helpers.
│   ├── contracts.py                AnalyzerContext + sub-info dataclasses.
│   ├── tables.py                   TA_CLASS_MAP, TA_PERIOD_ARG,
│   │                               TA_TUPLE_RETURNS, TA_MULTI_CTOR,
│   │                               TA_NO_CTOR, BUILTIN_VARS, BAR_FIELDS,
│   │                               SKIP_FUNCS.
│   ├── types.py                    Type-inference utilities.
│   └── diagnostics.py              Analyzer warning/error emission.
└── codegen/
    ├── base.py        (~6.0k loc)  CodeGen class — class-member layout,
    │                               include set, _matrix_vars / _array_vars
    │                               / _map_vars detection.
    ├── emit_top.py                 #include block, extern "C" wrappers,
    │                               strategy_create / run_backtest_full /
    │                               strategy_set_input layouts; the
    │                               strategy_declares_bar_magnifier()
    │                               export of a script that declares
    │                               use_bar_magnifier = true (the host
    │                               picks the feed and run parameters).
    ├── visit_stmt.py               Statement-level visitors (var decl,
    │                               assign, if/for/while/switch).
    ├── visit_expr.py               Expression-level visitors (literals,
    │                               operators, ternary, member access).
    ├── visit_call.py   (~3.6k loc) Function-call dispatch — by far the
    │                               most heavily-special-cased file.
    ├── ta.py                       TA call-site allocation + compute()
    │                               vs recompute() emission.
    ├── security.py    (~7.8k loc)  request.security / _lower_tf plumbing.
    ├── tables.py      (~1.2k loc)  Static dispatch tables: BAR_BUILTINS,
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
    ├── session_market.py           session.ismarket of a chart bar in a run
    │                               without a timeframe: the engine's session
    │                               calendar asked at the bar's open time,
    │                               cached per strategy (a run with one reads
    │                               the kernel's session_ismarket_).
    ├── input.py                    input.* / `input()` lowering.
    ├── constant_fold.py            Bounded numeric folding of constructor
    │                               expressions, without Python execution.
    ├── drawing.py                  Drawing objects as data (line / box /
    │                               label / linefill arenas) and their
    │                               lifetime rule (DRAWING_LIFETIME_CPP).
    ├── helpers_syminfo.py          C++ helpers deriving syminfo fields
    │                               from the SymInfo struct.
    ├── host_members.py             GENERATED by scripts/gen_host_members.py:
    │                               the host members the generated class
    │                               reads (reserved script names).
    └── helpers.py                  CPP_RESERVED + small text helpers.
tests/
├── _compile.py                     Helper that runs `g++ -fsyntax-only`
│                                   against the engine headers; reads
│                                   PINEFORGE_ENGINE_INCLUDE (else a sibling
│                                   checkout), PINEFORGE_EIGEN_INCLUDE,
│                                   PINEFORGE_GENERATED_INCLUDE,
│                                   PINEFORGE_ENGINE_LIB and CXX.
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
│                                   every dispatch lane; 5 regression
│                                   tests for once-broken paths
│                                   (year(time), matrix-returning methods,
│                                    str.format double-wrap, a cloned
│                                    no-ctor TA through a dead caller,
│                                    top-level fixnan vs a function's).
├── test_compile_corpus.py          Parametrized over every
│                                   corpus/<bucket>/<strategy>/strategy.pine
│                                   from a sibling pineforge-engine
│                                   checkout; collection size follows
│                                   the checked-out public corpus (314 of
│                                   its 325 strategy.pine files at engine
│                                   main 35db01c8: the 11 in deeper
│                                   directories are not collected).
├── test_official_surface.py        Locks SUPPORTED_* and signatures.* to
│                                   the Pine v6 official inventory
│                                   (sourced from user-pinescript-docs MCP).
├── test_ta_official_surface.py     Same idea, ta.*-specific (predates
│                                   the cross-namespace file).
├── test_support_checker.py         Per-rule support-checker behaviour.
├── test_codegen_new.py     (~2k loc)  Substring assertions on emitted C++.
├── test_signatures.py              Signature-registry unit tests.
└── test_{lexer,parser,analyzer,symbols,errors,…}.py
```

## Architectural invariants — do not break

1. **Exact engine release pairing.** From 1.0.0 on, bumping `VERSION`
   requires the matching `pineforge-engine` tag (`X.Y.Z` with `vX.Y.Z`;
   prereleases exactly); the 0.x releases pair as "What this is" states.
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
   `corpus/*/*/strategy.pine` file in the checked-out public corpus. That
   glob misses the strategies in deeper directories
   (`special-validation/<market>/<strategy>/`,
   `validation/symbol-specified/<symbol>/<strategy>/`: 11 of 325 at engine
   main 35db01c8), which this sweep does not check.
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
| `HARD_REJECT_FUNC`               | Calls with no PineForge semantics at all (e.g. `request.seed`). |
| `HARD_REJECT_NAMESPACE`          | Whole-namespace rejects — currently EMPTY (the old `ticker.*` blanket reject became per-function `HARD_REJECT_FUNC` entries: `ticker.{renko,kagi,linebreak,pointfigure,new,modify}`; `ticker.inherit`/`ticker.standard` pass through, `ticker.heikinashi` allowed for the chart's own symbol). |
| `DIVERGENT_VARS` (warning) / `DIVERGENT_VARS_ERROR` (reject) | Built-in variables whose value can diverge from TV. `bar_index` and `last_bar_index` warn because they depend on the fed data window; `last_bar_index` lowers to the window's true final index. The ERROR subset is reserved for silent mis-aliases and is currently empty. |
| `BARSTATE_APPROX_VARS` (warning) | Barstate flags PineForge approximates in batch mode.                 |
| `STRATEGY_UNSUPPORTED_PARAMS`    | Per-strategy.* call kwargs that codegen drops silently.              |
| `NOT_YET_FUNC`                   | Implementable but currently no codegen — reject loudly.              |
| `SUPPORTED_*` frozensets         | Per-namespace whitelist of names codegen knows how to emit.          |
| `varip` declarations             | Lowered as `var` (a historical bar executes once), with its storage and first-run latch left out of the calc_on_order_fills rollback checkpoint: TradingView rolls `var` back before a fill's recalculation and leaves `varip` alone. `tests/test_e2e_varip_coof.py` replays TradingView tapes with calc_on_order_fills on and off (`fixtures/popfix_tv`). A `request.security` helper-local `varip` is still refused. |
| `var` initializers | A `var` initializer runs once, in statement order, on the first bar that reaches its declaration: a primitive `var` at its declaration under a one-shot flag. One read with history (a `Series<T>` member) keeps the first-bar preamble only for a top-level declaration of a constant, a string literal, `na` or a bar field (`_series_var_init_keeps_preamble`, byte for byte); one in a block, whatever its initializer, reads na history until the first bar that reaches it (the preamble pushed bar 0's value: a block's `var float fc = close` held the first bar's close), and any other initializer -- a UDT field (`var int signal = direction.neutral`), a global computed before it (`var float first2 = b`), a call, `ta.*`, an input's override, a `switch` / `if` selection (the preamble's spelling of one did not compile; `tests/test_e2e_cgint5_compositions.py`) -- runs at its declaration too, the preamble pushing its na until then. The preamble used to push the analyzer's spelling ahead of the body: raw Pine that did not compile (`signal.push(direction.neutral)`, the ten ai-distribution probes) or a global before it was assigned (0), and an input's default. `tests/test_e2e_series_var_init.py` replays TradingView's tapes, an override and a block included (`fixtures/silent_tv`). |
| `request.security` helpers | A payload inlines the user functions and typed user methods it calls on the requested bar, under a builtin call too (`nz(f())`, `close.g()`, `nz(ta.sma(close, 3))`: the expression visitor that renders the builtin hands every user call, TA site, helper-bound name and requested-bar history back to `_build_security_expr`). Under a builtin, and for a method, only a single-expression body is inlined, every user call in it too, also through a global's value (`_security_body_is_expression`); a multi-statement one keeps the chart call and warns, because the evaluator writes its statements ahead of the payload, where a ternary arm or an `and`/`or` operand would run them on every bar. A TA site reached through several helper bindings gets one variant per binding, numbered (`_v0`, `_v1`, ...) by where each binding's value is written (`_security_variant_order_key`; the signature names nodes by `id()`, whose order is memory's; `tests/test_security_variant_order.py`). A helper called on its own result (`u(u(close))`) gets a variant per written call: the variant walk's recursion guard is keyed by the call, not the helper's name, which skipped the inner call as recursion and left it reading an undeclared base member (`tests/test_e2e_security_nested_helper_call.py`). A helper local's value is re-walked under the helper-name guard still (`_security_local_rewalks`): the evaluator reads the local's C++ variable, and entering the helper again there walked every earlier local once per read, 2**n walks over a chain of n locals. A call of a pure helper (one expression over its parameters, the requested bar's fields and literals, through operators and such calls) on arguments of that kind holds nothing a prepass collects, so the payload's walks stop at it; once its inlined text is 256 characters long its value is computed once where the evaluator opens (`_pf_shared_<N>_<k>`, `_security_share_pure_call`) and every reach with the same arguments reads it, where a diamond of such helpers inlined its leaf once per path (`tests/test_security_helper_diamond.py`). A payload's `bar_index` is the requested bar's, the count of requested bars before it (`_sec<N>_bar_index_`, advanced where the evaluator opens a requested slot; `bar_index[k]` k requested bars back, na before the first; `tests/test_e2e_security_bar_index.py`). A global the payload reads at an offset (`g[1]`) whose value is a user function's call or a numeric operator expression keeps its history on the requested clock: the value the payload reads as `g`, pushed once per completed requested bar where the evaluator closes (a helper inlines the read at each read of its parameter), and the payload lowers such a global once per evaluator (`g - g[1]` inlined the call at each read, which advanced its TA state once per read). The push takes the value of a read that ran, as an inline expression's history does: a read in a ternary arm, an if branch or a loop that did not run on a requested bar pushes nothing there, where TradingView's history holds every requested bar (a limit). The once-per-evaluator lowering holds within the block that emitted its locals: a later read outside a helper's if branch or loop lowers the call again, as every earlier build did at each read (it named the locals out of scope, which did not compile), except that a call with state (a TA site, a `var`, a request, a user method) whose global the payload also reads with history on the requested clock is refused where two of its lowerings can run on one requested bar, since its state would advance twice a bar (every earlier build refused that history read; one under a builtin keeps the chart's series and never counts, and the two arms of one if never run together). The builder refused the call's history ("helper call history is only supported in the payload itself") and indexed the operator expression's C++ scalar, which did not compile (`_security_global_history_value`). That history is a double (a bool's a bool), whose na reads na -- typed from the global's `int`, the first requested bar read `na<int>()` as -2147483648 and a 64-bit integer ("Epoch ints": `g = bar_index * 86400000`, or an epoch) wrapped -- and an integer slot of the evaluator (a helper's `int` local, an int tuple element) takes it narrowed na-preserving (quirk 9; an int reaching the chart's double still reads `na<int>()` as a number, as on main). Only a read lowered on the requested clock is so: one under a builtin (`nz(g[1], 0) / 2`) keeps the chart's int series and main's lowering, and a string global's history there is refused, as every earlier build refused it (`tests/test_e2e_cgint7_compositions.py`). A `request.security` of Heikin-Ashi bars inside the payload reads the requested context's Heikin-Ashi bars on TradingView, which the evaluator does not build: evaluating it stops the run (`_security_nested_heikinashi_request`), so a selection that never takes it (`useHA ? request.security(ticker.heikinashi(syminfo.tickerid), timeframe.period, close) : close` at its default) runs on the requested bars, where rendering the call read the chart's own request. An `int` / `float` cast whose argument reads a helper's parameter or local (a TA length `int(math.round(_len / 2.0))`) is the builder's own where the TA constructors are planned: the expression visitor rendered the name as an unknown variable. `tests/test_security_global_history.py` replays TradingView's tapes (`fixtures/tail_a_tv`). A global under a builtin call (`nz(s)`) is re-evaluated on the requested bar as a bare payload global is, when the expression map holds it and it is neither replayed state nor a per-run value (`_security_fallback_owns_global`); its history there (`nz(s[1])`) keeps the chart's series and warns (`tests/test_e2e_security_global_under_builtin.py`). All or nothing per evaluator (`_emit_security_evaluator_requested`): when a builtin call also reads something else on the chart's terms (a global it does not re-evaluate, history the builder does not own, `timeframe.*`: `_security_root_chart_read`) beside a call the evaluator inlines, or it refuses such a call (a shape a bare payload refuses), the evaluator keeps the lowering every earlier build emitted, its calls on the chart, and warns; a method with a non-scalar receiver or argument keeps the chart call and warns. A global that a top-level tuple declaration binds to a TA tuple call's element reads that field of the requested result; one bound to another tuple (a user function's) is refused there, as is history of a TA tuple's element, both of which used to emit C++ that did not compile. History of a helper call inside a helper body the payload inlines (`h() => nz(g()[1])`) is the requested bar's, where the payload reaches it once; one it reaches twice (`h() + h()`) is refused where it is lowered (`_collect_security_expr_hist_subscripts`; `tests/test_e2e_security_helper_body_history.py`). Under a builtin, TA tuple element history, read directly or through a global's value, is refused where every earlier build pushed the whole result into a double history; where one compiled (a multi-statement helper reaches the tuple, the read is a user call's argument, or the tuple is a user function's) it keeps the chart's series and warns. An inlined helper's local takes `na` of its own type and narrows to int or bool through quirk 9's na-preserving cast. A helper body may hold declarations, tuple declarations (`[m, s, h] = ta.macd(...)`, a nested helper's tuple), assignments, if-branches and bare expression statements; a local holding a `switch` whose arms are single expressions lowers to their ternary chain, which evaluates only the arm its selector takes (`tests/test_e2e_security_helper_switch.py`: it rendered as `/* unknown */`, which did not compile); a `for` or `while` loop (with `break` / `continue`) runs on the requested bar: its counter is a local, `o[i]` reads the requested bar's open `i` requested bars back, and a `for` keeps Pine's direction, step magnitude and `to` refresh; a loop holds plain locals only, since the evaluator computes a TA call, pushes a history-read local and keeps a `var` once per requested bar (a TA call, a `var`, a local read with history, a `request.*` / `strategy.*` call or a user call in a loop is refused by name: "request.security helper loops cannot hold ..."; `tests/test_e2e_security_helper_loops.py`, `fixtures/tail_e_tv`), and so is one reaching the loop through a name the lowering expands where it is read: a helper parameter's argument (`h(ta.sma(close, 3), n)` computed the SMA once per iteration, and so did a TA variable, `h(ta.accdist, n)`), or a user call or a `request.*` / `strategy.*` call in a global's declaration (`cs = sm(close)`), whose own TA sites are computed once, before the payload, and read freely; a name a loop statement declares is the loop's own in its block after its declaration (its initializer and a read after its block read the enclosing name); a `for` loop's counter is a counted loop's binder for the 64-bit rule, as on the chart ("Epoch ints"; `fixtures/cgint7_tv/cgint7_loop_products`), while a product with another helper-bound operand, and a `while` loop's counter, keep the payload's 32-bit spelling (a limit); a `for ... in` loop, a `switch` statement and a block arm stay refused. Helper `var` state (int, float, bool, string: strings in `_security_helper_series_str_`) belongs to its call site and advances once per requested bar; its family is read in the epoch-only mode (`_security_helper_var_state_type`): a width only the 64-bit arithmetic rule ("Epoch ints") gives it, its own or a same-spelled name's in another callable (`g() => n = days * 86400000` beside a helper's `var int n`), keeps the int family, whose double series holds the value exactly (the arithmetic width refused such a script, which compiled before; `tests/test_e2e_cgint6_compositions.py`), while an epoch, or an int-literal product past int32 in its own initializer, reaching it stays refused, as it always was. A helper tuple mixes int, float, bool and string elements, each stored in its own family: before the first requested value and on a `gaps_on` bar completing none, a bool element reads `false`, a numeric one `na` and a string one empty; a string payload returns a `std::string`. A TA history index is a literal or a bar-invariant int over inputs; an input it or a helper's TA constructor argument reads is its getter, a never-reassigned `var` input's too (`_security_var_input_call`; `tests/test_e2e_security_var_input_length.py`), since `evaluate_security` can run before `on_bar` reads the inputs; a payload divides two ints as floats. A TA length chosen by comparing an `input.string` with its options (`mode == "A" ? 5 : 10`) is input-derived, on the chart and in a payload (string literals are spelled in `_arith_expr_to_str` and blanked out of the reset path's identifier scans). The evaluator's prologue orders its TA variants so each follows the variants its arguments read, all at the evaluator's top level (a later global's TA read by a helper's TA was computed inline and again, twice a bar; `_security_prologue_order` follows what each variant's arguments can reach, and learns the reads by building each variant's arguments once, every side effect undone, only where that graph has a cycle; `tests/test_security_price_history_and_later_globals.py`, `fixtures/xsym_a2_tv`, lane CG-XSYM-A2). `tests/test_e2e_security_*.py` replay eight TradingView tapes (`fixtures/security2_tv`). A helper local a requested TA length reads is a value of each requested bar when it is a `var`, rebinds from its own value (`c += 1`, `c := c + 1`, or through another local: `b = a` then `a := b + 1`) or is reassigned in an if arm (a local the arm declares shadows it there): `ta.highest` / `ta.lowest` / `ta.highestbars` / `ta.lowestbars` re-window every call with it, and any other `ta.*` is refused naming the local (TradingView refuses such a length for `ta.ema`; PineForge does not lower its `ta.sma`). The planner bound each to one expression: `c += 1` to the literal 1 (a constant length, lane CGINT4), `c := c + 1` to an expression reading itself (Python recursed until its stack ran out); `tests/test_e2e_security_helper_var_length.py` replays TradingView's tape (`fixtures/silent_tv`). |
| `request.security` contexts | A request's symbol and timeframe are registered before the first bar. When they reach it through helper parameters, `security_contexts.specialize_security_contexts` (between the support check and the analyzer) resolves them on every call path from the top level, each parameter read in its caller's scope, down to globals, inputs, literals and built-ins: one context on every path is annotated on the request, several copy each helper on the paths once per context (`h__pfctx1`, ...; a copy's locals and loop variables are renamed too, `v__pfctx1`, since the analyzer keeps one history buffer per callable local name), and a parameter carrying one is declared `string`. It used to register the chart's timeframe (`input_tf_`), silently. A request whose timeframe or symbol is a bare parameter bound at every call site to a global expression keeps the analyzer's call-site clones (`callsite_idx`), which register a literal as a literal and anything else, an input too, as that expression (an input used to register its default, so an override was ignored), and a symbol clone its call site's Heikin-Ashi flag; a helper whose calls all pass one context is emitted once and its calls name it (they named a `f_cs0` nothing emitted: `f(string sym) => request.security(sym, "60", close[1])` did not compile; `tests/test_security_single_context_helpers.py`). A context no registration can compute (a series, a reassigned local, a user call, a method or overloaded helper on the path, too many paths) is refused by name; only a dead helper still registers the chart timeframe. A parameter of the helper that the request's payload reads is part of the context too, resolved the same way with bar series and user calls allowed: the request is copied per value and each copy's payload reads the value in the parameter's place. The evaluator never bound one: `nr(_s, _tf, _e) => request.security(_s, _tf, _e[1], ...)` read a member nothing pushed (`na`), a bare `_e` was an unknown variable, and `f(3)` and `f(6)` shared one `ta.sma(close, len)` built from the first call's length. Only a value the builder lowers on the requested bars in the read's place is put in (`_Lowered`): chart series, `ta.*` and `math.*` calls, inputs (an untitled one titled with its declaration's key), `input.source` values and stable globals built from them, a global declared after the helper too when it is a number or a bool (the copy's read carries its binding, `GLOBAL_ANNOTATION`, where the analyzer binds none: it read the chart's value; the analyzer types the name a float, so a later string keeps its refusal); a history object is a bar or price series (`hl2` to `hlcc4` too), a `ta.*` call (inline or a global's value), an `input.source` value, an inline operator expression, or a global holding a numeric operator expression or a call of a user function the builder inlines whole (one definition whose body declares plain locals and ends in an expression over its parameters, bar series and literals, through operators, single-valued `ta.*`, `math.*`, `int` / `float` / `nz` and such calls: `_inlined_function`), such a call's global also as a value; no global under a builtin rendered on the chart's terms. Any other value keeps its lowering, never a refusal, and a history read of such a parameter warns that it reads `na` (not in a helper nothing reaches). A length every top-level call passes one value keeps the analyzer's single evaluator; its call-site clones for differing timeframes built every clone from the first call's length. A Heikin-Ashi symbol alias declared after the helper keeps its flag. `tests/test_security_contexts.py`; `tests/test_e2e_security_nested_contexts.py` replays TradingView's nested-helper tape (`fixtures/xsym_tv`), `tests/test_security_payload_params.py` two payload-parameter tapes. |
| Requests with no data | `request.financial` / `earnings` / `dividends` / `splits` / `footprint` (`NO_DATA_REQUEST_FUNC`) and `request.security` on another symbol, whose value reaches display and alert sinks only or that read no recorded series or feed (the next rows), read data the engine does not load. `external_requests.TradeSlice` follows the value forward: one that reaches alerts, plots, tables and logs only -- through operators, na-safe built-ins, user functions and bindings, `var` too -- is lowered to na by `lower_no_data_requests` (between the support check and `security_contexts`; a helper's bool tuple becomes `[false, false]`) with the WARNING "value reaches only display/alert sinks; lowered to na; trades are unaffected". Every other one -- it can reach an order, `runtime.error`, a collection, a drawing, a method, a history offset or a `break`, or its arguments do anything but compute -- is a deferred refusal: it warns "no data is pinned for this request; the run stops with an error where its value is read" (the use named in the hint) and lowers to a value whose first READ stops the run with that request named. Binding it is no read: the reads of a declaration holding the whole request (its names never reassigned) stop the run, else evaluating the request does. TradingView never evaluates a `switch` arm its selector does not take (a default arm beside one stopping the script books its trades), and a script reading a request only in such an arm runs. TradingView's tapes of a strategy with an alerts-and-table watchlist and of the same strategy without it are byte for byte one tape on BINANCE:ETHUSDT.P 15 and NASDAQ:AAPL 15 (`tests/test_external_requests.py`, `fixtures/xsym_tv`). |
| Requests of other symbols | A `request.security` of another symbol whose value can reach a trade reads the feed a probe's requests manifest pins for it (`PINEFORGE_REQUESTS_ROOT`; the engine's instrument feeds, `FEED_LOWERING`), with the WARNING "another symbol's bars, read from the feed the requests manifest pins for it; with none installed, the run stops with an error where its value is read". `configure_security_evaluators()` registers it by its symbol string as the run computes it before the first bar -- a literal, an input's getter, the chart's `syminfo.*` strings and operators and ternaries over them (`ScriptIndex.registration_value`), or a helper's parameter resolved on every call path (`security_contexts` owns such a request whatever its timeframe, so three symbols at two timeframes through three helper levels are six contexts) -- and its `ignore_invalid_symbol` (na for a symbol whose facts say it is invalid; without it the run stops): on the chart when the string is the chart's (the one the manifest resolves it to, else the chart's ticker id or `syminfo.ticker`), on the installed feed of (symbol, timeframe) otherwise, and not at all without one, when its reads stop the run as a deferred refusal does (`_pf_sec_missing_N`), never reading the chart. Its payload runs on that symbol's own bars: history, `ta.*` state, `bar_index`, `time_close` (the host's, the feed's close) and `syminfo.*` are the requested context's, and `ta.ema` there is SMA-seeded (na until `length` values) as TradingView's is; an int tuple element is held as an na-preserving double (a millisecond stamp overflows an int); under gaps_on a chart bar the feed hands nothing reads na and the context's history carries on. A symbol that can select the chart's or another symbol's (`useOther ? "TVC:DXY" : syminfo.tickerid`) registers the same way when its value can reach a trade: it read the chart's bars for both arms; through a helper's parameter that registration cannot compute on some path, it keeps that chart lowering and its warning. A symbol registration cannot compute (a series, a reassigned name, a method's parameter, a block's local, a path the pass cannot follow), a timeframe it reads before the first bar with no value yet (a `var`, a reassigned name, a series: `ScriptIndex.registers_timeframe`; it registered the chart's), and a request inside another request's expression, written there or in a helper the expression calls, keep the deferred refusal, with a warning naming the path. `ticker.inherit(from_tickerid, symbol)` names its symbol second (`ticker_symbol_arg`; it was read as the chart's). A `request.security_lower_tf` of another symbol whose value can reach a trade stops the run where it is evaluated (`ABSENT_LOWERING`: it read the chart's intrabars); one that can select the chart's keeps its lowering and warns. A tuple request whose names are reassigned stops where it is evaluated (the tuple emitter never visited it, and a missing feed read na). A payload that is `request.footprint(ticks, va)` (literal whole numbers) reads the feed's `fp_delta_<ticks>_<va>` column on the requested bar (`FOOTPRINT_COLUMN_ANNOTATION`), a feed without the column being missing data, the chart's own symbol too (the chart's bars carry no footprint: it read the chart, and na); a footprint value is its delta -- a declaration or helper parameter typed `footprint`, one holding the request or a helper's call returning it, a parameter a call passes one to -- so `fp.delta()` and `footprint.delta(fp)` read it (`read_footprint_deltas`: they did not compile) and every other `footprint.*` member is refused by name. TradingView's footprint delta of a range depends on the range (the month slices of a year disagree), so a footprint feed holds for the window it was captured over, where a synthetic BITSTAMP:BTCUSD delta filter on BINANCE:BTCUSDT 15 books its 1674 trades exactly. `currency=`, `ticker.new` and `ticker.modify` stay refused. A `barmerge` constant is a value (on 1, off 0), held and compared (`var gapStrategy = gaps ? barmerge.gaps_on : barmerge.gaps_off`, `TradingView/Request/3`); a request's gaps and lookahead read the constant as written: a computed one keeps a chart request's refusal, and makes a request of another symbol read no data (its first read stops the run) with its registration naming why (`fixtures/tail_f_tv/barmerge_values`). TradingView tapes of synthetic strategies (a TVC:DXY EMA cross on NYSE:F 15, BINANCE:BTCUSDT 1D and BINANCE:ETHUSDT.P 15, TVC:VIX 240 `ta.ema(close, 14)[1]` lookahead_on on NASDAQ:AAPL 1D and OANDA:XAUUSD 15, gaps_on on NYSE:F 15 and ETHUSDT.P 15) match trade for trade on the XSYM-B captures, and the XSYM-DESIGN alignment witnesses bar for bar; they stay outside this repository with the captures. `tests/test_foreign_requests.py` replays the rules on generated bars. |
| Recorded requests | A `request.earnings` / `dividends` / `splits` / `financial` whose value can reach a trade reads the series TradingView returned per chart bar, which the requests manifest records (`chart_open_ms,value`, every bar TradingView answered non-na) under `<fn>|<symbol>|<field-or-id>|<period-or->|gaps_<on|off>|lookahead_<on|off>` (`RECORDED_LOWERING`, the workflow's `formatRecordedKey`): TradingView's report-time and fiscal-period logic is recorded, not modelled. The key's symbol is the string the run passes (`syminfo.tickerid` is the lane symbol, the key the capture records), its other parts constants of the call: the `earnings.*` / `dividends.*` / `splits.*` field (default `actual` / `gross` / `denominator`), a literal financial id and period (`FQ`, `FY`, `FH`, `TTM`), `barmerge.*` gaps and lookahead (default off; `request.financial` has none). `_pf_recorded(key, _pf_rec_missing_N)` reads the value of the chart bar that opens at the tape's time, na elsewhere, and sets the request's flag from the key computed where the request is evaluated, which its reads test (a key re-rendered at a read, in a helper, read the helper's names), with the WARNING "TradingView's values per chart bar, read from the series the requests manifest records under ...". A key nobody installed reads na where the request is bound and stops the run where it is read, as a deferred refusal does; where TradingView answers na throughout (fundamentals of a crypto chart, an arm a probe never takes) the capture records a header-only tape, which reads na and runs. A spelling no key names -- a field or id that is not a constant of the grammar, a `currency`, a request inside `request.security`, written there or in a helper its expression calls -- keeps the deferred refusal. A PEAD-like synthetic strategy books TradingView's trades exactly on NASDAQ:AAPL 1D (4/4) and NYSE:F 15 (5/5) from the XSYM-B tapes, kept outside this repository; `tests/test_recorded_requests.py` checks keys and replay on generated tapes. |
| Switch arms | The support checker visits every arm of a `switch` -- its condition and its block -- as it visits an `if`'s: the generic child walk skipped `cases`' `(condition, block)` tuples, so every arm but the default went unchecked, and a request of another symbol in one kept no lowering (the request contexts then refused it as a chart request they could not key: `TradingView/Request/3`'s `cryptoDerivativeMetric`). Such an arm gets its lowerings; what the checker would refuse there (an unsupported call, a text constant held as a value) warns instead, and keeps the lowering it compiled to, so a script that compiled keeps compiling (`SupportChecker._switch_arm_depth`). The C++ of the 1,384 population sources was unchanged by it (2026-09-29 census). |
| Block locals | Every declaration of the script's top level, direct or in a top-level block, is held in the class member of its name. One in a block whose type that member cannot hold (a string beside a number, a collection or an object beside another type) gets a name of its own, with every read of it in its block (`k__pfblk1`): Pine scopes it to its block. `CodeGen.block_locals_needing_names` names them and `transpile()` runs once more (`block_locals`); a counter `k = 0` beside `k = array.get(names, i)` did not compile (the fruit-fly probes), and a later declaration's type leaked into an unrelated `b = k * 10`. Numbers and bools share one member, as before (`fixtures/tail_f_tv/block_local_types`). |
| Array arguments | A callee's array parameter is `std::vector<T>&`: Pine arrays are references, and a callee's change to a variable's or a field's array reaches the caller. A fresh array -- a matrix row or column, `array.copy` / `from` / `new*`, `str.split`, a function's new array -- is a C++ temporary, which no `T&` takes: the call is staged (`_ordered_user_call_expr`) so it binds to a named forwarding reference, and the callee's changes stay in it, as TradingView's do (`thequantscience/XGBoostMini/1` passes `matrix.row(X, i)` to a method; `visit_call._binds_fresh_array_to_reference`). A user function's array that a variable may hold (it returns its argument or a global) and a selection of a held and a fresh array are refused: TradingView passes that array itself, where PineForge would pass a copy, and neither compiled (`fixtures/tail_f_tv/array_ref_args` / `array_ref_fresh`). Any other argument keeps the lowering it had (a variable, a field, `na`, a built-in such as `request.security_lower_tf`, held in a member). |
| Int products | Pine's `int` is 64-bit. A product of two operands emitted as C++ `int` -- int literals, names stored as `int`, loop variables, `array<int>` elements -- that a `%` or `/` reads is computed in 64 bits, `((int64_t)(a) * (b))`: both operators lower through doubles, which hold the product whole, where the C++ `int` product wrapped -- the fruit-fly probes' Park-Miller noise step `(s * 48271) % 2147483647` (`fixtures/tail_f_tv/int_product` reads 2076553157 on the sixth bar; `visit_expr._lower_binop`). A product whose operands' bounds can leave int32 is computed in 64 bits by the "Epoch ints" rule whether a `%` or `/` reads it or not, na-aware, and keeps that form, cast once (`_lower_binop`'s `widened`: this rule cast it a second time); this rule covers the rest a `%` or `/` reads, an operand those bounds do not know (`array.get`) or a product they keep inside int32. A product neither rule reaches keeps its 32-bit spelling (a limit: an `array.get` product past int32 that no `%` or `/` reads wraps, where TradingView reads the 64-bit value). A `request.security` payload's `%` and `/` follow the "Epoch ints" rule alone (`tests/test_e2e_cgint7_compositions.py`, `fixtures/cgint7_tv/cgint7_int_products`). A literal tree is folded as before (see "Epoch ints"); an int emitted as a double (`math.round`) keeps its product. |
| `time()` / `time_close()` offsets | A `bars_back` / `timeframe_bars_back` that is not a literal 0 (`time("", "", -1)` is the next bar's open) reads another bar's time through the host's `pine_time_offset` (engine lane TAIL-E; the pair must build together): `bars_back` chart bars back, or forward when negative, then the `timeframe` bar holding that chart bar, stepped `timeframe_bars_back` of its own bars, a future bar included, as TradingView reads it (`visit_call._time_offset_call`, which binds the three overloads by their argument types, `_bind_time_call`; `tests/test_time_bar_offsets.py`, tapes `fixtures/tail_e_tv/te_time_bb_chart` / `te_time_bb_tf`). Inside `request.security` it is refused. It was refused everywhere (the engine had no API for another bar's time), and before that reached the C++ as the timezone string. |
| Version directive                | The first line holding only `//`, `@version`, `=` and ASCII digits, with optional spaces, tabs or form feeds around each, anywhere in the script (inside a multiline string too), lines ending at `\r\n`, `\r` or `\n` -- TradingView's rule on 46 probes (`tests/test_version_directive.py`). `@Version`, `///`, trailing text, code or other comment text before it on the line, a vertical tab or no-break space, `6.0` or a carriage return inside it make it no directive. Lexer gaps, not the directive's: the lexer ends a line only at `\n`, so text after a bare `\r` on a comment line stays comment (TradingView reads it as the next line), and it refuses a form feed before `//` as an unexpected character. Only v6 is supported. |
| Library imports | `import <user>/<name>/<version> [as <alias>]` parses into its parts (the parser used to join the line: `…/2asml`). An import whose alias -- else the library's name -- is `ta`, `math` or `str`, while every member the script names through it, in a call, a read or a type, is that namespace's built-in, is a no-op: TradingView's pine-facade compile of `import TradingView/ta/7` links no library when only built-in `ta.*` names are called, and links it for a library-only name (`ta.dema`). Every other import is refused, naming it, unless its library's source is at hand: `transpile(..., libraries={"user/name/version": source})`, or with `libraries=None` `$PINEFORGE_PINE_LIBRARIES` (case-wide `libraries.json` + `<user>/<name>/<version>.pine`) through the script's OWN manifest `$PINEFORGE_REQUESTS_ROOT/<slug>/requests.json` (the private pineforge-workflow repository's `docs/xsym-requests.md`; `pine_libraries`). The verifier calls `transpile(read_text())` with no slug (the private pineforge-lab repository, 3bac0b7b `scripts/verify-engine-local.py:1698`), so the manifest is the one whose `probe.strategySha256` is the source's sha256, as read or with every LF spelled CRLF or CR (`read_text()` folds both); none or several resolves no library, and a neighbour's pin in the case-wide directory never does. An import the manifest does not pin, a missing source, a sha mismatch or a non-open library is refused by name; with neither the argument nor the variable the refusal is today's (`tests/test_pine_libraries.py`). A resolved library is inlined before the support check (`library_inline`), so its code meets every rule the script's does: only the exports the script reaches and what they reach (transitively, through the library's own imports) enter the program, and library example code (plots, inputs) never does; every inlined top-level name, local and parameter gets a module-qualified name (`HanJinSignals26_v15__pinbar`, `..._pinbar__up`), a keyword argument follows its parameter; `alias.f(...)`, `alias.T.new(...)`, `alias.E.member`, `alias.C` and `alias.T` in a type resolve into the library; an exported method keeps its name (methods bind by receiver type) and `alias.m(recv, ...)` is `recv.m(...)`; an alias equal to `ta`, `math` or `str` reads the built-in member when one exists, else the library's; an inlined function is a user function, so its state is per call site. A library keeps its own `//@version`: a v5 one (the open `jdehorty/MLExtensions/2` and `jdehorty/KernelFunctions/2`) is lowered by v5's rules inside its bodies (`library_v5.V5_RULES` gives every change the migration guide to v6 lists a disposition): two const ints divide as ints, rounded toward zero; `and` / `or` evaluate both operands; a `for` end is fixed before the first iteration; `color.red` / `teal` / `yellow` keep their v5 values; `timeframe.period` reads `D` / `W` / `M` on a 1D / 1W / 1M chart; a negative index to `array.get` / `set` / `insert` / `remove` stops the run; a bool na reads false where v5 casts it and across the call. What v5 reads differently and PineForge does not implement is refused naming the library: an observer of the bool na (`na()`, `nz()`, `fixnan()`, `==`, `!=`, `str.tostring` / `str.format` of a bool), `request.*()`, history of a literal, a built-in constant or a type's field, a reassigned variable as a `ta.*` / `math.sum` length, a division whose constness depends on an untyped parameter, and a v5 function reached from a `request.security` payload (`tests/test_library_v5.py`; `tests/test_e2e_library_v5.py` replays TradingView's 15m and 1D tapes of `xc_v5_lib`, the synthetic `pftest/V5Rules/1` as a v5 strategy, `fixtures/xsym_lib_tv`; `tests/test_e2e_color_tapes.py` the engine's tape of every named color read by a v5 strategy, `w11-color-v5-eth15`, through the synthetic `pftest/W11ColorV5/1`: a `//@version=5` script itself is refused, so a v5 library is the only v5 code the transpiler lowers). An import the script never uses links nothing (TradingView prunes it) and is dropped. An overloaded function binds, per call, as TradingView binds it: of the overloads its arguments fit by count and keyword, those that differ by their parameters' qualifiers alone (`TradingView/Request/3` overloads `cryptoDerivativeMetric` with `simple string` and `series string` parameters) give the one with the weakest qualifiers its arguments fit, an argument being const or simple when it is a literal, an input, a `syminfo.*` / `timeframe.*` member, a script declaration of one (declared once, never reassigned, not `var` or `series`) or a library parameter declared so, and series otherwise; each later overload gets its own name (`Request_v3__cryptoDerivativeMetric_2`, `library_inline._Linker._overload`); a keyword argument binds by the parameter's name as the library spells it, whichever function the inliner renamed first (a library's own `f(n = 2)` read `f`'s default once `f` was processed) (`tests/test_tail_f_rules.py`; `fixtures/tail_f_tv/overload_qualifiers`: a literal, an input and a declaration of an input call the `simple int` overload, `bar_index % 5` and `minute(time)` the `series int` one). Overloads that differ by type, an unexported member, an alias equal to another built-in namespace or to a script name, and a method duplicating a receiver type's method are refused by name (`tests/test_library_inline.py`; `tests/test_e2e_library_inline.py`: a script importing the synthetic `pftest/Signals/1` books as the same code written in). TradingView's tapes of synthetic scripts calling `richardgong1988/HanJinSignals26/15` on NASDAQ:AAPL 15, OANDA:EURUSD 15 and NYSE:F 1D are reproduced exit Signal for exit Signal outside the repository, where the library is pinned as evidence; that of one calling `jdehorty/MLExtensions/2`'s `n_rsi` and `jdehorty/KernelFunctions/2`'s `rationalQuadratic` on BINANCE:BTCUSDT 15 exit for exit, under the verifier's chart EMA warmup candidate, every Signal but its `color.t` field (a transparency the engine rounds up: 91 where TradingView reads 90). TradingView's tape of a script with that import is byte for byte the tape without it (`tests/test_import_builtin_namespace.py`, `fixtures/xsym_tv`). |
| TF literal validation            | `request.security` / `request.security_lower_tf` `timeframe` string literals validated against Pine v6 format at parse time. |
| `ta.vwap` anchor                 | Omitted-anchor VWAP and its band form keep `ta::VWAP` / `ta::VWAPBands` exactly. Every explicit scalar or band anchor, including `timeframe.change("1D")` / `("D")`, is passed per bar to the TA1 anchored VWAP shim; a TU built against an older engine falls back to the historical session-day lowering. TradingView tapes for both a mid-session and a day-boundary start show the explicit daily form is `na` until the first day change, while omitted-anchor VWAP is finite from bar 0. |
| Input titles (codegen)           | `_check_input_titles` refuses a title that is not a compile-time string constant (TradingView: `title (const string)`) before generation; PineForge keys every override by the title. |
| Input keys (codegen)             | `_check_input_keys` WARNS, once per override key that several inputs share (the title, else the name of the declaration holding the call: two untitled calls in one declaration, two outside any declaration, an untitled call and another titled with its name, a title repeated across `group=`s), naming every input the key reaches: one override sets all of them. TradingView tells such inputs apart, so the script transpiles unchanged (closed strategies 125-cleightyp and 162-nicocashfx repeat titles across groups and grade excellent at their defaults). |
| Parser syntax                    | A `ParseError` at top level or inside a block raises a located `CompileError` instead of discarding tokens. Adjacent expressions on one line (`x = 1 2`, `s = "a" "b"`) are rejected at the second token. The 312 validation probes, 602 corpus `.pine` files and 101 closed strategies had zero parser recovery events at the 2026-09-24 census; no validated script lost support. A `switch` arm written on its `=>` line is a comma statement list, as a block's line is: TradingView runs the statements left to right and the arm's value is the last one's, a declaration's or an assignment's too (`=> runtime.error(...), ""`, the open `TradingView/Request/3` and `TradingView/LibraryCOT/5`; `parser._parse_arm_line`). A lone expression arm is the node it always was, and a call before the next arm's `=>` is never a function definition (`tests/test_tail_f_rules.py`; `tests/test_e2e_tail_f_tapes.py` replays TradingView's tapes, `fixtures/tail_f_tv`, a script and `pftest/ArmLine/1` alike). |
| Bare `ta.tr`                    | The variable equals `ta.tr(false)`: it is `na` when the previous close is `na`, including bar 0. The explicit `ta.tr(true)` and `ta.atr` paths retain their own first-bar behavior. TradingView's 2025-04-01 `c6-ta-tr-bar-zero` trade booked Signal `bare-na`; `tests/test_e2e_bare_ta_tr.py` compares the emitted bar traces and trades with `ta.tr(false)`. |
| `ta.*` call arguments            | `_check_ta_arguments` binds every `ta.*` call to TradingView's signature (`signatures.TA_FUNCTIONS`: TradingView's parameter names, e.g. `series` for `ta.alma` / `bb` / `bbw` / `cmo` / `kc` / `kcw`, and required/optional split) and refuses what TradingView rejects: an unknown keyword, an argument too many, one given twice or a missing required one -- each used to be dropped, shifted into the next constructor slot or passed to a `compute()` that does not exist. `ta.vwap()` with no argument reads as the bare property, as it always has. |
| `ta.*` TA1 arguments | The TA1 engine computes `ta.alma` `floor`, `ta.kc` / `ta.kcw` `useTrueRange`, explicit `ta.vwap` anchors, and `ta.pivot_point_levels` `anchor` / `developing` exactly. The generated TU selects those APIs with `PF_ALMA_HAS_FLOOR`, `PF_KC_HAS_USE_TRUE_RANGE`, `PF_VWAP_HAS_ANCHOR_INPUT`, and `PF_PIVOT_LEVELS_HAS_ANCHOR`; when a macro is absent, the shim falls back to the historical lowering. Only omitted-anchor VWAP keeps `ta::VWAP` / `ta::VWAPBands`; every explicit anchor, including `timeframe.change("1D"|"D")`, uses `ta::AnchoredVWAP` / `ta::AnchoredVWAPBands`. The pivot free function remains only for literal/aliased `anchor=true, developing=false`; every other spelling uses `ta::PivotPointLevels`. |
| `ta.*` lengths the constructor cannot take | A length (or `ta.supertrend` factor) that is neither a compile-time constant nor an input/timeframe expression the runtime reset re-reads is lowered by its Pine qualifier, as TradingView answers it (lane K-TA-DYNLEN tapes; rules in the engine's `pineforge/source/pine_ta_length.hpp`). A simple length -- fixed for the run: a syminfo preset, `str.*`/`math.*` over those, expanded context-free so a `request.security` payload evaluates it too; a name counts only where it reads the top-level declaration, so one a block, loop or callable binds, or a value declared `series`, is series -- builds the constant-length class on the call's first execution (`pineforge::source::FirstCallBound<class>`, the length checked by `simple_ta_length`: TradingView answers a simple length exactly as the constant, a sparse window through the constant ring). A series length of `ta.highest` / `ta.lowest` / `ta.highestbars` / `ta.lowestbars` re-windows every call (`pineforge::source::Series*`). `ta.supertrend` passes factor and atrPeriod on the call and reads them once (`pineforge::source::PineSupertrend`: TradingView compiles a series factor but uses the first execution's). A series length of any other family keeps the refusal: TradingView refuses it for `ta.rma`/`ema`/`rsi`/`atr`/`dmi`/`macd`/`kc`/`hma`/`tsi`/`rci`, and the other window functions (`ta.sma`, `ta.wma`, ...) are not lowered yet. Each `request.security` copy is planned from the length its own call passes (a helper call's binding, a per-call-site clone) and reads `timeframe.*` in the requested timeframe; a callable's one evaluator refuses call sites that pass different lengths, where a length the `request.security` contexts row copies the request for (a literal, an input, an `input.string` choice) gives each call path its own evaluator instead. Constant- and input-length calls keep their constructor byte for byte; an `input.string` choice of an int length the signature table knows is one (the `request.security` helpers row). Every other argument its spelled string literals would bring to the constructor -- a `syminfo.*` or `timeframe.*` comparison, or an `input.string` choice of a float parameter or of one the table does not know (a VWAP band multiplier, `math.sum`'s length) -- stays here, spelled from its declarations (`_ta_arg_takes_plan`): the reset path reads a payload's `timeframe.*` on the chart's timeframe and casts its expression to an int length. One this lowering cannot spell stays refused, and a callable's one evaluator still refuses call sites that pass different such lengths. `tests/test_e2e_ta_dynamic_length.py` replays five TradingView tapes, `tests/test_codegen_ta_dynamic_length.py` pins the C++. |
| Chart EMA warmup | The generated `_PFKC` / `_PFKCW` shims scope the engine's EMA warmup selector around their internal EMA basis. KC/KCW middle is `na` until the Pine length is warm, including bar 0, without changing unrelated chart EMA call sites. A covered TradingView tape records both KC middle and an independent EMA as `na` on bar 0 and first finite at bar 19 (length 20). The standalone engine EMA remains finite on bar 0, so `ta.ema` warns about that warmup approximation; `tests/test_kc_middle_band.py` pins the limited scope and warning. |
| Color na conversions | Packed colors use `int64_t` storage, including typed scalar, array and matrix paths, so their `na<int64_t>()` sentinel survives. `array.from` of colors is a `std::vector<int64_t>`: a color literal, a `color.*` constant, a `color.new` / `rgb` / `from_gradient` call, a conditional selecting one or a script variable bound to one types the element on both sides (`_is_color_value`, the analyzer's `_is_color_constructor`); it was read as a float, and `var array<color> A = array.from(...)` did not compile (`tests/test_e2e_color_arrays.py`, tape `fixtures/silent2_tv/cgs2_color_arrays`). `color.new` preserves an `na` base; `na` transparency becomes 100; an `na` `color.rgb` channel becomes zero. The covered TradingView color probes are pinned by `tests/test_na_truthiness.py`. A finite transparency reaches the engine's `new_color` as a double, whose alpha byte is the nearest to 255 * (100 - t) / 100, as TradingView's for an input or a series transparency and `color.rgb`'s: `color.new(c, t)` at t = 10.5 reads back 11 (a truncating cast read 10; an integer literal keeps its `(int)` spelling). TradingView truncates a constant `color.new` transparency (`color.new(color.red, 10.5)` and `10.6` read 10, `99.5` reads 99), which PineForge still reads through the byte (pinned: `tests/test_e2e_cgint6_compositions.py`, tape `fixtures/cgint6_tv/cgint6_const_transp`). `tests/test_e2e_color_tapes.py` replays the engine's `w11-color-v6-eth15` tape (`fixtures/color_tv`). `color.new(color, transp)`, `color.rgb(red, green, blue, transp)` and `color.r` / `g` / `b` / `t(color)` bind a keyword argument to its parameter's slot, in parameter order (`_color_arg_nodes`; a keyword transparency is converted as a positional one): a keyword was dropped, so `color.new(c, transp = 40)` lowered to the color 0 (`tests/test_e2e_color_keyword_arguments.py`, tape `fixtures/silent2_tv/cgs2_color_keywords`). |
| Epoch ints | A Pine `int` slot an epoch reaches is `int64_t`: a name whose declaration or any reassignment gives it a wide value (an epoch builtin, a wide name or parameter, or an expression `_expr_returns_wide_int` traces to one), and a declared-`int` or untyped parameter a written call feeds one (`types._wide_int_provenance`, a fixed point keyed by spelling). The width used to stop at the first copy: `int sel = switch k => E2` over `const int E2 = timestamp(...)`, `alias = firstT` over `firstT := time`, and `f(int t)` called with `time` held 32 bits (1743468300000 read -288422176), and `int(x)` of an epoch casts to `int64_t` (`int(time)` was a 32-bit cast). A function's or method's local declared `float` keeps a float series when an epoch reaches it (`float x = time`: `_callable_series_local_symbol` reads the function's scope, `method_<Type>_<name>` for a method, which the global lookup missed): its `Series<int64_t>` read the integer sentinel for a missing `x[1]`, so `x - x[1]` was a garbage number on the first bar instead of na (`tests/test_e2e_float_time_local.py`, tapes `fixtures/silent2_tv/cgs2_float_time_local` and `cgs2_float_time_method`). `tests/test_e2e_krunerr_int64_provenance.py` replays TradingView's tape (`fixtures/krunerr_tv`). A method call's arguments do not widen its parameters. Integer arithmetic over literals and the constants emitted as literals keeps 64 bits too: `400 * MS` over `const int MS = 7200000` is 2880000000 (it was the C++ `int` product, -1414967296), folded where the tree is literal after the names are inlined (`_pure_int_literal_value`, `_inlined_int_constant`) and where the emitted C++ is (`_fold_int32_overflow_cpp`: a `request.security` payload expands a global into its declaration), and the slot a top-level declaration of an int constant past int32 initializes is `int64_t`, reassigned or not (`g = 3000000000`, `var int e = 300 * MS`: `_literal_wide_global`); a literal's width (`g = 3000000000`) reaches a declaration reading it while it is never reassigned (`h = g`) but does not travel by spelling into a reassignment (`k := g`), parameters, arrays, `request.security` helper state or a callable's local of the same name, which keep the types they compiled with, while constant arithmetic (`300 * MS`) is a 64-bit product too, whose width travels as the next sentences say. `tests/test_e2e_int64_constants.py` replays TradingView's tape (`fixtures/silent_tv`). An integer `+ - *` those folds leave, whose operands are both 32-bit C++ ints, is computed in 64 bits at run time when its magnitude can leave int32 (`_int_arith_leaves_int32`: literals and inlined constants exactly, an `input.int` by its declared range, else its default and 2**24, `bar_index`, a counted loop's binder, a `for ... in` binder whose spec is `int` (an int element, map value or index; a float element is a double: `v * 1000` over one was truncated) and any other runtime integer name 2**24 (a call's result does not widen); a callable's name resolves in its own callable, a declared `int` local included, whichever callable is being emitted; a reassigned top-level constant past int32, whose slot is `int64_t`, is no 32-bit operand, and an input's named bound reads the top-level constant), and its value is an epoch-like wide value for the slots above: `days * 86400000` over `days = input.int(30)` and `bar_index * 7200000` overflowed. An operand that can be na (a parameter, a local, a script variable) makes the value na: a double (`_wide_int_arith_cpp`, exact to 2**53; the widening cast read `na<int>()` as -2147483648) that every integer store narrows na-preserving, a slot, a parameter, an array element, a map or matrix value. In a `request.security` payload a side the builder re-evaluates as a double computes in double and a name a helper binds keeps its spelling; the rest widens as on the chart, and the payload's copy of a script variable only such arithmetic makes wide is `int64_t` too (`_security_copy_is_arithmetic_wide`, stored na-preserving, a compound `%=` through `std::fmod`); an epoch's or an int-literal product's keeps its `int` copy, as it always did. A tuple element and a function's or method's result holding a constant past int32 (`_holds_wide_int_constant`), and a read of a UDT `int` field (`int64_t` storage) some write fills with one (`_wide_udt_int_fields`), are `int64_t` too. A `map<..., int>` value, `matrix<int>` element or element of an int array only such a value reaches (only an epoch widens an array, `_wide_int_array_names`: a declared `array<int>` parameter, result or field does not bind a `std::vector<int64_t>`) keeps its C++ `int` (its type also types the handle's parameters and its `values()` / `row()` arrays), narrows the value na-preserving and warns where it stores one (`tests/test_e2e_int64_products.py`, tapes `fixtures/silent2_tv/cgs2_int64_products`, `cgs2_int64_followup` and `cgs2_int64_followup2`). |
| Array and matrix history | `a[k]` of an array or a matrix variable is TradingView's read-only copy of the collection as the variable left it at the end of its scope's execution k executions back: the bar k bars back for the top level, its block's previous run for a block's local (`tests/fixtures/array_history_tv`). A `var` array's history is the copy, one element shorter than the array it grows (`ahist_ref`), and a `var` matrix's holds the previous bar's value (`ahist_mtx`). The variable keeps its own `std::vector` or matrix member, so every other operation keeps its C++; `_pf_collection_hist_<name>` (`_PFCollectionHistory`, `pineforge_codegen/collection_history.py`) keeps the copies of one declaration: each execution of the declaration (each bar, for a top-level `var`) opens a slot and the bar's end closes it with a copy. Sibling blocks declaring the same name share its member, so each declaration gets a history of its own (`_pf_collection_hist_1_<name>`, ..., which no Pine name spells) and first closes the others' with the array their block left (`ahist_sibling`: one shared history read the other block's array, 336 of 336 exits wrong). `CollectionHistoryChecker.decide` gives every read one outcome. Supported, for a variable of the top level or one of its blocks: a built-in reads it (`(a[1]).size()`, `array.get(a[1], 0)`, `(m[1]).get(0, 0)`, `matrix.det(m[1])`; a matrix function's new matrix has the variable's type, `matrix.copy(mi[1])` of a `matrix<int>` too); a built-in that changes it stops the run with TradingView's RE10051 text (`(a[1]).push(x)`; a zero offset is the variable itself, which changes); `na()` asks whether a copy exists; a `for...in` loop over it iterates the variable's current array, as TradingView's does (`ahist_loop`: `for v in b[1]` sums the current array); a variable bound to it or a user function given it that only reads it holds a copy (an empty array before the variable has a history, where TradingView's is na: PineForge holds no na array). A method on a na history stops the run (RE10052, RE10053). Refused by name, what TradingView refuses: a method straight after the history (`a[1].size()`: CE10011), an object's array, matrix or map field's history (`h.xs[1]`, `(h.xs)[1]`: CE10290, in the analyzer), an array where a number, a condition, a string or an element is expected (CE10123: an operator, `nz`, `math.*`, a value slot of an array function such as `c.push(a[1])` or `array.new<float>(2, a[1])`, `log.info`'s message, `label.new`'s text, `str.tostring` of colors; CE10173: a scalar variable or field; CE10101: an if or while condition; CE10122: `array.from`, `str.format` of colors; CE10009: a bare statement of the top level). Every other read keeps the lowering it had where that compiled -- an element of the current array (`na()` and `str.tostring()` in a function, a request payload, a loop, a block's `var`; a variable bound to the history that only those read; a selection's history), a parameter's current array (a namespace call, a for...in loop, a typed copy), each with a warning, and anything in a function no call names, which the codegen does not emit (it emits every method and every function a call names, an uncalled function's call included: a read there is decided as any, and one an uncalled function hands to such a function's untyped parameter by how that body uses it) -- and is refused where it did not, as probes of every use and every array function in every scope and for every element type against the previous build showed (`_ELEMENT_LOWERING_FAILS` and its siblings): a collection use of a number element (a method, any `array.*` read but `array.copy` into a namespace call or a loop's local, a typed parameter or binding, a loop over a number) and the string ones `std::string` lacks, `na()` or `str.tostring()` of a parameter's array and its array-returning reads, a change through a variable or a slice bound to the history (TradingView stops there, RE10051; PineForge would change a copy), a binding read both as an array and through `na()`, a matrix's history outside the scopes kept, `matrix.sum` of a history (its matrix result never compiled), a call's result's history. Whether an earlier lowering compiled often turns on the consumer of an array function's value, which the tags carry (`_array_result_consumer`: a method on the new array, a function returning it, rendering it, a member holding it; `_value_consumer`: a string value returned or bound), and an overloaded method given the history keeps the earlier lowering. `xs[k] := v`, which TradingView refuses (CE10009, CE10013), keeps its element assignment. An object's array or matrix field read through the object's history (`(h[1]).xs.size()`) is the field as it is now (`ahist_field`), as it compiled before. Map history stays refused. Two gaps keep the C++ the previous build emitted, which does not compile: an element function's value of a string array's history the earlier lowering reads (`f() => str.length(array.first(s[1]))`: a character of the current array's string, held as a number, which most string consumers do not take; the probes measured a value bound or returned), and the history of a variable or a call the analyzer does not type as an array or a matrix (a method's or `matrix.row`'s new array, a selection of arrays, a tuple's element, an array of drawings), which the checker never sees. `tests/test_array_history.py` compiles every shape and pins the refusals and the earlier lowerings; `tests/test_e2e_array_history.py` replays six tapes and the runtime stops. |
| syminfo na-gap warning           | `SUPPORTED_SYMINFO` = every `SYMINFO_MEMBER_MAP` key, but members whose emission is `na<T>()` or a `get_syminfo_metadata(...)` lookup (root/pricescale/minmove/mincontract/current_contract/expiration_date/isin/sector/industry + fundamentals/recommendations/target_price_*) form `_SYMINFO_SILENT_GAP_FIELDS` (derived from the emission table, so new na-accept fields can't drift out): every read WARNS that the value is na until a data feed injects it. `syminfo.timezone` spells an empty or `UTC` exchange zone `Etc/UTC`, TradingView's name for BINANCE's (`syminfo.timezone == "Etc/UTC"` holds there, `== "UTC"` does not); the engine calls that take the symbol's zone keep the engine's name (`tests/test_e2e_syminfo_timezone_spelling.py`, `fixtures/tail_e_tv`). `syminfo.mincontract` (`_SYMINFO_RUN_FACT_FIELDS`) reads the symbol fact the run declares as syminfo metadata `"mincontract"` (`runtime_overrides.syminfo_metadata`, a lane fact beside `qty_step`) and warns that it is na without one; lab tv read-outs give TradingView's values (0.0001 ETHUSDT.P, 0.00001 BTCUSDT, 0.01 EURUSD/XAUUSD, 1 on the stock, index and futures lanes; `tests/test_e2e_syminfo_mincontract.py`). |
| Session flags | A chart bar's `session.ismarket` reads the kernel's in-session fact `session_ismarket_`, which reads the bar at its interval's first eligible instant: its open, or the reopen for a bar that opens in a break (TradingView's TSE:7203 and CBOT:ZC1! 60-minute bars that open at 12:00 and 08:00 are in market); a D/W/M bar is in market. TradingView flags a bar by its open time (on extended-hours NASDAQ:AAPL 60 the 09:00 bar, which holds the 09:30 open, is pre-market and the 16:00 bar post-market), reads a day mask per session day, and flags OANDA:XAUUSD's daily bars, stamped at the 17:00 ET break, in market. A run with no timeframe (one bar, none given: `script_tf_` is empty) gets no session-day facts (`session.isfirstbar` / `islastbar` read false there, as the test pins); there the emitted `_PFSessionMarket` type and its per-strategy `_pf_session_market_` cache (`codegen/session_market.py`, outside the checkpointed script state) ask the engine's session calendar (`native_calendar::session_day_at`) about the bar's open time. Both replaced the time-of-day predicate, which missed every Sunday-evening open under a `:23456` mask and every `0000-2400` bar until engine lane W11-ENG-TIME-COLOR read a mask by each window's session day and `2400` as the day's end (the predicate now reads those tapes as TradingView does); the calendar at the bar's open, the chart's lowering before engine lane K-SESSION-WINDOWS, puts the 10 break-open bars of each 60-minute tape out of market. A bar in market is in neither `session.ispremarket` nor `session.ispostmarket`; off it, the engine's windows decide (`session_in_premarket` / `session_in_postmarket`, src/session_time.cpp): from 04:00 to the session day's first open and from its last close to 20:00, over every window in either order; a bar between two windows is neither, and so is every bar of an overnight or 24-hour session (engine lane K-SESSION-WINDOWS; the windows used to read the first window only, and held 98-117 in-market bars of each ES1! / XAUUSD tape). TradingView's two-window charts TSE:7203, HKEX:700 and CBOT:ZC1! hold no off-market bar and flag none pre- or post-market (`tests/test_e2e_session_windows.py`). A `request.security` payload's own session reads, a user function's it inlines included, keep the time-of-day predicates at the security bar's open time, a D/W/M bar's too, which TradingView reads as whole session days, and WARN. `session.isfirstbar` / `islastbar` read the host's `session_isfirstbar_` / `session_islastbar_`, the chart's session day, which the engine widens by the pre- and post-market bars an extended-hours chart holds (the extended-hours tape's 04:00 and 19:00 bars), and the `_regular` spellings its `session_isfirstbar_regular_` / `session_islastbar_regular_`, the regular day (10:00 and 15:00; they used to read the chart's pair). Under TSE:7203's published 15:30 end the regular day closes on the 15:15 bar, where TradingView's closes on its 15:30 bar (pinned). `tests/test_e2e_session_ismarket.py` replays 15 TradingView tapes (`tests/fixtures/session_ismarket`) and `tests/test_e2e_session_windows.py` the three two-window charts at 15, 60 and 240 minutes, on their own bars and, for TSE:7203 and CBOT:ZC1!, aggregated from 15 minutes (the engine aggregates HKEX:700 on a 09:30-anchored grid, not TradingView's clock hours), each replay ending on the bar TradingView's chart holds next (a batch's final bar closes its session day); TradingView's HKEX:700 bars above 15 minutes, whose session day it gives two first bars (60) or makes every bar first and last (240), are pinned. |
| Session flag history | `session.<flag>[k]` is the flag k bars ago at the top level of a script, in a block and on a lazy operand too, and k calls ago inside a function, each call site apart and whichever operand or block of the function holds the read, as TradingView's NASDAQ:AAPL 15 tapes with and without extended hours show (`cgs2-hist-*`, `cgs2-histfn-*`); a read before the first bar is false. A top-level read indexes the flag's `_pf_session_hist_<flag>` Series, pushed at the top of every chart bar. A function that reads a flag at an offset is emitted once per call site (the analyzer's synthetic-history statefulness) and pushes the flag into its `_session_call_*` Series at entry, once per call. A `request.security` expression that reaches the read through its own operators keeps a bool `_sec<N>_expr_hist_*` Series on the requested clock (a function whose only read sits in its own request.security keeps no per-call Series), as does a builtin call's argument the payload builder lowers on the requested clock (`nz(session.ismarket[1] ? 1.0 : na)`: the `request.security` helpers row); one that reaches it through a variable, or through a call's argument where the evaluator keeps the chart's terms, and a read in a function or method such an expression evaluates (the analyzer follows its calls, the variables it reads and their statements), is refused at the read. The codegen also refuses a read it emits in a method (a call on a receiver the analyzer cannot type, such as `mk().m()` or a loop variable, is not told apart) or in a function a method, such an expression or a UDT field default reaches (`session_history_unsafe`; the analyzer follows a global's name as the evaluator does, whatever a function binds itself). Both follow the emitted C++, not the rendered reads (`_settle_session_reads`): a refused read renders as a stand-in name, refused only if the code outside comments and literals holds it, so a read the C++ never holds (in `plot`, `alert`, a drawing's style or xloc, `color.from_gradient`, any argument the codegen drops or renders and leaves out) refuses nothing: such callables get no clone, and `transpile()` first analyzes a script without the clones of functions that read a flag at an offset, then again with the functions whose reads that C++ holds cloned as well, until it asks for no more (a clone can make a caller's read reach the C++) (`pineforge_codegen/session_reads.py`, `_generate`), so the others keep the calls they compiled with and never cost a clone (a clone could also put a wrapper's int and float calls on one variant, and a function called along a deep call tree gets one per path); a top-level one may leave an unused Series. It used to index a C++ bool and fail the compile. `tests/test_e2e_session_history.py` replays the three tapes. |
| Drawing lifetime | A deleted drawing reads like a na handle: every getter returns na, every setter does nothing and `na()` is true (a na handle's getter reads na too, instead of halting the run). A new line, box or label that makes its kind's live count reach `max_<kind>_count + 6` (default 50) deletes the oldest drawings of that kind no `var`/`varip` or history-read variable holds, until `max_<kind>_count` remain; a drawing held only by an array, an object field, a local or a plain non-var variable is collectable, and linefills are never collected. The engine's arena reads a deleted record's data and evicts at exactly the cap, so the generated arenas are unbounded and the TU carries the rule (`DRAWING_LIFETIME_CPP` and the `_pf_collect_<kind>s_` members, `codegen/drawing.py`). `tests/test_e2e_drawing_lifetime.py` replays 15 TradingView readout tapes (`tests/fixtures/drawing_lifetime`) value by value. |
| Object and drawing history | A variable of a user-defined type or a drawing type (`line`, `box`, `label`, `linefill`, `chart.point`) holds a reference, and its history holds the references it held: `(c[1]).v` reads the object `c` held one bar back as it is now (a change made through `c[1]` or after its bar shows), a `var` object's history is the object itself, and `c[1]` is na on the first bar (TradingView's tapes, `tests/fixtures/udt_history_tv`). Such a variable whose history is read is a `Series` of its handles (`Series<Cell>`, `Series<Box>`; na is `T{}`) -- a global, a `var`, a block's or a function's local, a history parameter and a method's receiver alike (`_series_handle_type_name`, `_series_param_element_cpp_type`): it was a `Series<double>` the handles were pushed into, which did not compile. A drawing parameter or receiver read so takes its built-in methods (`(x[1]).get_top()`, emitted `None()`). Inside a function, a method and an `if` block the history counts the executions of the scope, as TradingView's does; a parameter's at a call site that skips bars is the chart-aligned hold-last clock #109 gave plain functions (`udth_clock`), which a typed method's receiver does not keep yet (its bridge pushes per call: `udth_clock_method`, pinned). A temporary drawing passed to a drawing parameter, which a callable takes as `T&` -- a history read, a history-read variable's current slot, a new drawing or chart point, a user call's result -- is bound through the staged call (`_binds_temporary_drawing_to_reference`, as a fresh array is); it did not compile. A field chain visits its receiver (`(o[1]).inner.v` registered no history and read the current object). The history of an expression whose value is an object -- a field holding one (`o.inner[1]`), a function's result, a ternary's selection -- is the reference it produced at its previous evaluation (`_history_value_cpp_type`); below a lazy edge one safe to evaluate (a pure or object-constructor call over pure arguments, a ternary over names, a named object's field, na while the object is) runs on every execution of its scope, the bars that skip the arm included, as TradingView's does (`_lazy_reference_history_object`, `udth_lazy`). `==` and `!=` compare line and label references by identity, na included (a deleted one keeps its own); every other object or drawing comparison is refused, as TradingView refuses it (CE10123), and so is a comparison with na (CE10187). Refused as TradingView refuses them: a field or a method straight after the history operator (`c[1].v`: CE10011, `b[1].get_top()`: CE10010; `(c[1]).v` is the spelling), and the history of a field holding a value (`c.v[1]` and `(c.v)[1]`: CE10290). Refused here: the history of a reference inside a `request.security` expression (TradingView reads the requested timeframe's objects, which PineForge does not keep), and the history of a `chart.point` in a script that changes a `chart.point` field (PineForge holds a point as a value). A field read of a na object stops the run, as TradingView's does (RE10041). `tests/test_e2e_udt_history.py` replays eleven tapes; `tests/test_udt_history.py` compiles every shape and pins the refusals. |
| `request.security` payload sources | A source input (`src = input.source(ohlc4, ...)`, a bare `input(close)`, a global name bound to one) read in a payload is the series the input selects, on the requested bar: `get_input_source` resolves the override or the default to one of the chart's source series, and the payload reads that series of `bar` (`_security_source_input_expr`). It used to read the chart's value, so `ta.rsi(src, 3)` in a payload ran on the chart's ohlc4. Its history and `hl2` / `hlc3` / `ohlc4` / `hlcc4` history advance once per requested bar like `close[1]`'s (`_sec<N>_hist_<field>`, one member per source input), directly, under a builtin call and through a helper parameter: `src[1]` was refused, `hl2[1]` read the chart's `_s_hl2`, and `hlcc4[k]` read the current bar beside a chart push of `current_bar_.hlcc4` that does not compile (on the chart too). `tests/test_e2e_security_payload_sources.py` replays three TradingView tapes (`fixtures/security_sources_tv`). A helper parameter a source input is bound to is put in by `security_contexts`, where the requested bars read it as above (lane CG-XSYM-A2): `h(_v) => request.security(syminfo.tickerid, "240", ta.sma(_v, 3))` called `h(src)` read an unknown variable `_v`; `tests/test_security_input_source.py` replays two more TradingView tapes and, under overrides, their twins with other source defaults (`fixtures/xsym_a2_tv`). |
| Timeframes computed on the first bar | The engine registers every request in `configure_security_evaluators()`, before the first bar. A timeframe's globals are expanded into their declaration expressions (inputs read through their getters); a global the script reassigns (`lowerSeconds := math.max(60, lowerSeconds)`) used to render as its member, which holds its initial value then (iamalala registered "1" for TradingView's "72"). Its first-bar value is now computed there (`_security_tf_replay_prologue`): locals shadowing the members, assigned by the top-level declarations (`var` too), reassignments and `if` blocks that give them their value, in source order, from literals, inputs, `timeframe.*` / `syminfo.*` / `format.*` and pure `math` / `str` calls; a global built from a reassigned one is a local too, computed at its declaration. A name it cannot compute that way (a user call, a loop, a statement after the first request) keeps the registration every earlier build emitted, with a warning naming what stops it. `tests/test_e2e_security_ltf_computed_tf.py` replays TradingView's tape (`fixtures/security_ltf_tv`) with the corpus 1m feed as the lower-timeframe feed. |
| Epoch ints through `request.security` | The requested bar's value keeps its payload's provenance (`_expr_returns_wide_int`), and a tuple declaration binds each name to the element its tuple evaluates, through the payload and a user function's final tuple (`_tuple_element_value`): `var int last := request.security(t, "60", time)` and `f_ok(int t)` fed `t2` of `[t0, t1, t2] = request.security(t, "60", f_get())` are `int64_t`. They were 32-bit, reached through `(int)<double>` of the epoch, which x86-64 read as `na` and arm64 as `INT_MAX`: job-2868's latch and drjproduction's zones booked no trade on Cloud Run. A float or a bool is no epoch slot: `/`, a comparison and `and` / `or` end the provenance, and a float or bool name keeps its series type (`_series_type_for`; the analyzer types every request value float, which a double holds exactly). `tests/test_e2e_security_epoch_int64.py` replays three TradingView tapes (`fixtures/security_epoch_tv`) and scans the public sources for a security epoch narrowed to `int`. |


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
   function form via `visit_call.py` (a guarded `pine_hour(ts, tz)`),
   so the two forms agree for present timestamps. A function-call `na`
   timestamp uses epoch zero; `weekofyear(na)` returns 1, as covered
   TradingView UTC and New York tapes show. The two-arg form uses the explicit tz; the
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
   (`row`, `col`, `eigenvalues`) must NOT be in the set: `_type_spec_from_expr`
   types those as arrays in both the method and the namespace form
   (`matrix.row(m, 0)`), as it does the array methods that build a new array
   (`types.ARRAY_RESULT_METHODS`: abs, standardize, sort_indices), so an
   untyped LHS is declared a vector (`tests/test_e2e_untyped_collection_results.py`).
3. **`str.format` / `str.tostring` / `log.*` number text.** The emitted
   helper in `codegen/tv_number_format.py` takes typed format arguments and
   renders TradingView's `#,###.###` default for `str.format`, its
   `{0,number,...}` styles and apostrophe quoting, and `str.tostring`'s
   `#.##########` default, custom `#`/`0`/`%` patterns, percent and volume.
   `format.mintick` delegates to the engine's tick-rounding formatter.
   The helper is emitted when any syntax child calls a formatter
   (`iter_ast_nodes`: `_walk_ast` skipped a tuple literal, so a
   `request.security` tuple payload's `str.tostring` left it undeclared;
   `tests/test_e2e_tostring_tuple_payload.py`).
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
   `cs_info` / `_func_cs_var_remap`. A helper reached through a second call
   of its caller runs as a context-sensitive instance (`g__ni1`,
   `_build_func_instances`) with fresh copies of the path's TA, `var`,
   fixnan and history-read local (`f := ... f[1]`) state;
   `tests/test_e2e_nested_path_series.py` pins the locals. Such an instance takes the parameter and return types
   of the written call it runs, as that call's `_csN` clone does
   (`type_call_site_idx`): typed from the shared inference, an untyped
   string parameter was a double (`tests/test_e2e_nested_instance_param_types.py`).
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
   allowed. Another symbol reads its pinned feed, `ignore_invalid_symbol`
   honoured ("Requests of other symbols" above), or lowers to na or a
   deferred refusal ("Requests with no data").
   Chart-symbol aliases resolve by lexical
   binding, so a local rebind cannot taint an unrelated global. A helper
   parameter is the chart's symbol when every call of its helper binds one
   to it, through further helpers' parameters too (a method's or an
   overloaded helper's parameter stays refused), and each call path's
   symbol, Heikin-Ashi or plain, is part of its request's context
   (`tests/test_security_symbol_helper_param.py`). A ternary
   symbol that can select another symbol registers, when its value can
   reach a trade, by the string the run computes: the chart's arm reads
   the chart and another's its pinned feed, or stops the run where read
   (never the chart's bars in its place); one reaching display sinks only
   warns and keeps its chart-symbol lowering. Safe
   forms include `syminfo.tickerid`, `syminfo.ticker`, and
   `ticker.inherit/standard/heikinashi(<chart sym>)` (`ticker.inherit`'s
   symbol is its second argument). `gaps` and
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
9. **Preserve `na` at integer width boundaries.** A `double`
  expression reaching an `int` / `int64_t` slot, or an integer crossing
   between those widths, must go through
   `helpers.na_preserving_int_cast` (`is_na(_pf_v) ? na<int>() :
   (int)_pf_v`), applied by `types._coerce_int_slot`. An *implicit*
   narrowing of a NaN is undefined ([conv.fpint]) and the compilers
   disagree: AppleClang arm64 and g++ aarch64 give 0 at every `-O`,
   g++ x86-64 gives `INT_MIN` at `-O0`/`-O1` and 0 from `-O2`. The
   engine's contract (`include/pineforge/na.hpp`) is that an integer
   `na` IS `std::numeric_limits<T>::min()`, which is what `is_na(T)`
   tests. The cast helper checks the original numeric type before narrowing:
   converting `na<int64_t>()` to `double` first loses its sentinel. A
   `double` outside `int` narrows to `na<int>()` too (x86-64's `INT_MIN`; arm64
   saturated an epoch a missed provenance narrowed). Dynamic
   history, matrix and lazy TA indices use `pine_index_int_cast` for the same
   reason. One bench slot booked 2412 trades built at `-O3` and 2411
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
   the closed conversion-site check. A plain global int every binding of
   which is a one-argument `math.floor` / `ceil` / `round`, read by an
   ordering comparison and otherwise only by a comparison, `na`, `nz`,
   `str.tostring` or a `strategy.entry` / `strategy.order` quantity, is
   stored as the double its value is (`_nonfinite_int_names`, lane
   TAIL-C): TradingView keeps the +Infinity of a division by zero there,
   which `>` / `>=` order above every number while `==` / `!=` are false
   and every other read reads na (`_nonfinite_int_read`), and an entry
   quantity of it trades the default quantity (the engine's rule). The
   narrowing made it `na<int>()`, which ordered as na, so `if shares > 0`
   never held (`tests/test_e2e_nonfinite_int.py`,
   `fixtures/nonfinite_int_tv`). Any other int keeps its C++ `int`.
   The other direction: a non-constant integer reaching a user-defined
   record's `float` field (`Cell.new(v = bar_index)`, an epoch) is
   converted where the designated initializer would narrow it, an
   integer na to na (`types._coerce_double_slot`): it did not compile
   (`tests/test_e2e_udt_float_field_ctor.py`, tape
   `fixtures/array_history_tv/uctor_float`); an expression of int
   literals a double holds exactly keeps its spelling. A `:=` to such a
   field converts the same way: it compiled, but an int na became
   -2147483648 where TradingView's field is na (tape
   `fixtures/array_history_tv/uassign_float`).
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
   `tests/test_e2e_generic_input_length.py`), and it is the type of its
   default -- `input("x")` a string input, `input(3)` an int one,
   `input(true)` a bool one -- for its member, the static-input
   initialisation and the inline read alike (`_input_getter_for_call`; a
   string default assigned a `std::string` to a double member;
   `tests/test_e2e_generic_input_types.py`). The input manifest
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
   spelling against literal-length twins. A length read through a plain
   alias of an input-derived scalar (`emaLen = calcEmaLen` over
   `calcEmaLen = swingLen * emaRatio`) is rebuilt from the inputs too:
   the alias is a derived expression of the name it copies
   (`_collect_known_var`), where the reset read the alias's own member
   before the body set it, an EMA of length 0 for the whole run
   (`tests/test_e2e_alias_ta_length.py`, tapes `fixtures/alias_len_tv`).
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
    clock, whose eager chart `source[length]` fallbacks the tapes refute:
    before the first execution, and between executions closer than `length`
    bars, where the `w8a-lazy-mom-held` tape reads the held `close[4]` for
    `ta.mom(close, 3)`, `tests/test_e2e_lazy_held_source.py`);
    `cum`/`barssince`/`valuewhen`/`cross*`/`rising`/`falling`/`math.sum`
    (`LAZY_PER_EXECUTION_TA`) keep the reached-only inline compute, which is
    TradingView's per-execution clock, and never precalc.
    Sites inside `if`/loop/function bodies, `else if` conditions, `var`
    initializers, `request.security` payloads and tuple-returning sites keep
    their existing lowering, but for a `change`/`mom`/`roc` in a top-level
    if block -- an else-if's or a nested if's condition and statements, and
    a value-form if's -- which runs only on the bars its block runs and reads
    the same held source history (`_lazy_edge_ta_hoist_plan`: TradingView's
    `te_lazy_if_source` tape reads `ta.roc(close, 3)` in a nested if from
    the close its third bar held, where the executions' own ring read na;
    `tests/test_e2e_lazy_if_source_clock.py`); loop and switch bodies, a
    `var` initializer and a call read with history keep their lowering. The old "lazy SMA/EMA must not precalc" pins
    (pf-probe-oliver-dual-vol-sma) encoded the refuted per-call clock and were
    re-pinned in `test_codegen_validation_fixes.py`.
    A user function's call read at an offset below such an edge
    (`isNew(s) => inS(s) and not inS(s)[1]`) is the call's value k executions
    of its scope ago -- k bars at the top level, k calls in a function -- as
    TradingView's BINANCE:BTCUSDT 15 tape shows
    (`fixtures/lazy_call_history/tg-lazyhist-btc15`,
    `tests/test_e2e_lazy_call_history.py`): codegen pushes its `_hist_call_*`
    Series once per execution, before the statement, in a top-level statement
    and in a statement of a function body (`codegen/ta.py::
    _lazy_call_history_units`, `_emit_lazy_call_history_hoists`). Only a call
    of a pure function -- one expression of its parameters, bar fields,
    literals, `timeframe.*`/`syminfo.*`, operators and `na`/`nz`/`time`/
    `time_close` or such functions, with arguments of literals, names and
    operators -- is hoisted; any other keeps the call-local push where the
    operand runs, which reads the previous time it ran. An if whose head
    reads such a history and whose block calls a `change`/`mom`/`roc`
    takes both rules: the push before the if, the held source clock in
    its block (`fixtures/cgint7_tv/cgint7_lazy`,
    `tests/test_e2e_cgint7_compositions.py`).
16. **Every call argument is evaluated once.** Pine evaluates each argument
    of a call exactly once per execution; only `and` / `or` and the `?:` arms
    are lazy (a lab tv counting-probe tape, `fixtures/w2_trio_tv/eval_counts`,
    shows every builtin slot below once per bar). A lowering template that
    reads an argument twice, or once per loop iteration, re-runs its C++, so a
    stateful call in it (a `ta.*` compute(), a user function with state) runs
    more than once. `fixnan(x)` did that (TradingView's DMI/ADX helpers then
    double-stepped their RMA), and `nz(x, y)` ran `y` only on na bars. Bind
    such an argument first: `nz` and `fixnan` bind their own, and
    `helpers.evaluate_args_once` binds every argument a template reads
    repeatedly that is not a plain read (`cpp_is_plain_read`: a literal, a
    name or member chain, a literal-offset history read), which keeps plain
    reads byte for byte. `STR_ARGS_READ_REPEATEDLY` and
    `ARRAY_ARGS_READ_REPEATEDLY` (`codegen/tables.py`) list the table-driven
    slots (`math.round`, `str.substring` and `str.replace` bind where they
    lower); add one when a template reads an argument more than once.
    `color.from_gradient` is a warned visual-only stub yielding the na
    colour that still evaluates, once each in parameter order, every
    argument that can have an effect (`_may_have_effects`: any call but a
    pure builtin; `tests/test_e2e_from_gradient_arguments.py`).
    `tests/test_e2e_argument_evaluation_once.py` runs every slot through a
    counting probe and replays the tape; `tests/test_e2e_fixnan_single_eval.py`
    replays a TradingView ADX tape. Keyword arguments take TradingView's
    names -- `nz(source, replacement)`, `fixnan(source)`, `str.repeat(source,
    repeat, separator)` -- and TradingView evaluates them in parameter order
    whatever order they are written in: the support checker binds `nz` and
    `fixnan` to their signatures (`_check_builtin_arguments`, refusing what
    TradingView refuses) and `builtin_keywords` rewrites them positionally
    (`tests/test_e2e_builtin_keyword_names.py`).
17. **A function's last statement is its value.** TradingView returns the
    value of a function's (or an if/switch arm's) last statement, whatever
    the statement: `x := e` and every `x op= e` yield x's new value (typed as
    x: `analyzer._statement_value_type`), `obj.f := e` the field's, a
    declaration `[var] [T] x = e` the variable, a tuple declaration
    `[p, q] = f()` the tuple, and a `for` / `for ... in` / `while` loop the
    value its body's last statement produced on the last iteration that
    reached it (`na` when none did; an if without else or a switch without
    default ending the body is `na` after an iteration that ran no arm).
    `emit_top`'s function emitter and `visit_stmt._emit_body_with_assign`
    handle each through `_statement_value_node` / `_emit_loop_with_assign`,
    and return only a scalar or string value that fits the slot
    (`_tail_value_fits`, judged from the statement, not its emitted local):
    a drawing, UDT or collection handle keeps the statement-then-default
    lowering it always compiled to. Any other last statement falls through
    to the default return. An if without else (an else-if chain without a
    final else, a switch without default) that runs no arm is `na` -- a
    numeric or string na, false for a bool, `na<int64_t>()` for an `int` an
    epoch reaches (see "Epoch ints") -- as a function's last statement,
    nested in a taken arm, and as the value a global, reassigned or local
    variable takes, which does not keep its previous bar's value
    (`visit_stmt._visit_selection_value`;
    `tests/test_e2e_if_without_else_na.py`, tape
    `fixtures/open_items_tv/if_tail_na`).
    A tuple reassignment `[p, q] := f()` is a TradingView syntax error
    (CE10156) and is refused, as are a tuple literal as a variable's value
    (`[a, b] = [x, y]`, `t = [x, y]`: CE10156) and a ternary returning
    tuples (`support_checker._check_tuple_literal_value`,
    `tests/test_tuple_literal_value.py`). `tests/test_e2e_function_tail_value.py` replays
    seven TradingView tapes (`fixtures/w2_trio_tv/tail_*`) and compiles the
    handle shapes.
18. **A script variable read through history in a function body reads its
    call site's history.** TradingView builds the history of a series used
    in a function through each call of it (`lab tv`, BINANCE:ETHUSDT.P 15,
    `tests/fixtures/function_global_history`): in a plain UDF, `x[k]` on a
    script variable (`var` or not) or on `bar_index` is `x` as that call site
    saw it at its latest call at or before `k` bars ago -- one slot per chart
    bar, `na` before the first call, the value at the call even when `x`
    changes later in the bar or the function runs on every bar; each call
    site, a nested one included, has its own. Chart built-ins (`close`,
    `time`, `hl2`, ...) keep the chart's history. The analyzer records the
    reads (`func_global_history_reads` / `_nodes`) and marks the function
    stateful, so every call site gets its own body; each chart-executed body
    owns an `_fn_global_hist_N` Series on the `udf_series_arg` clock (advanced
    in the on_bar preamble, updated at the body's entry;
    `tests/test_e2e_function_global_history.py`). UDT methods, string/color
    variables and request.security-only bodies keep the chart's history. In a
    callable body the clone var remap lists every callable's members for
    nested-instance composition, so a read the analyzer resolved to a global,
    a loop binder or a parameter is not renamed (`_call_site_var_name`):
    renaming a script variable `src` to another callable's `src_cs1` read
    `na`. A callable stateful only through this rule keeps its old shared
    typing when two primitive types meet in one variant, with a WARNING
    (`tests/test_function_global_history_clones.py`).
19. **A binary operator evaluates its left operand first.** C++ leaves the
    order of the operands of `+ - * / %` and of an overloaded operator
    (`std::string`'s `+` and `==`, `std::fmod`) unspecified: AppleClang goes
    left to right, GCC on x86-64 ran `std::string` `operator+`'s right operand
    first. When one operand has an effect
    (`visit_expr._expr_has_ordered_effect`: an array/map/matrix mutation, a
    drawing, an order, a log line, an alert, `runtime.error`, or a user
    function or method doing one or assigning a UDT field) and the other can
    observe it (it is not `_binop_operand_is_order_free`: literals,
    variables, and `str.*`/`math.*`/`color.*`/`ta.*`/`nz`/`na`/cast calls and
    operators over them), `_left_operand_first` emits
    `[&]{ auto __pf_binop_lhs_N = (<left>); return <op>; }()` (`bool` for a
    bool operand: a `std::vector<bool>` element is a proxy), for
    `_visit_binop` and the `request.security` builder alike. `&&`/`||` and
    the relational wrappers already order their operands; every other binop
    keeps its C++ (the 325 corpus, 277 gate and 1,430 population sources are
    byte-identical). TradingView's K-RUNERR tape spells the left-first order
    (`tests/test_e2e_krunerr_array_negative_index.py`);
    `tests/test_binop_operand_order.py` pins the form and the values. A
    call's arguments are ordered only where a lowering binds them:
    `_ordered_user_call_expr` stages a user call with a map effect or a
    temporary receiver, `helpers.evaluate_args_once` (quirk 16) binds the
    arguments a template reads more than once, and the checked `array.*`
    lowerings bind receiver, index and value in turn.

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
`Level.WARNING` for divergences-but-not-broken, `Level.ERROR`
otherwise.
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
- **Helper underscores.** Codegen-internal helpers in `codegen/tables.py`
are underscore-prefixed (`_matrix_add_row`, `_merge_kwargs`); they
are not part of the package's external surface.
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
such names and keeps each built-in beside them: its NASDAQ:AAPL 15 tape
`fixtures/host_member_names/cgs2-hostnames-aapl-15-reg` is replayed bar for
bar. Rerun the script after a lowering starts reading a host member or the
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
- **Don't update `VERSION`** (from 1.0.0 on) without confirming the exact
  matching engine tag and its generated headers and static library are
  available.
- **Don't introduce runtime dependencies.** Pure-Python is the install
contract. Test extras (pytest) are the only allowed `[project.optional-dependencies]`.

## Parity campaign gate (applies on EVERY harness)

Pushes and PRs from this repo are gated by the PineForge parity campaign: a
fresh (≤6h) PASS verdict must bind the exact (engine, codegen) HEADs, recorded
on the campaign registry. The gate is maintainers' tooling: it lives in the
private `pineforge-workflow` repository. Under Claude Code a PreToolUse hook
(`.claude/settings.json`, calls `pineforge-workflow/campaign/hooks/pr-gate.mjs`
from a checkout at `~/code/pineforge-workflow`) enforces this on `git push` /
`gh pr create|ready|merge`. Codex, OpenCode, and other harnesses run NO hook —
the discipline is exactly as binding there: before any push, run the gate job
and record its verdict as the `pr-gate` skill in that repository's
`.claude/skills/` describes (plain markdown, readable anywhere), ending with:

```sh
lab gate record --verdict <verdict.json> --engine <sha> --codegen <sha>
```

Merged single-axis PRs advance the campaign baseline automatically
(`.github/workflows/promote-baseline.yml`) when the merge commit on `main`
carries the gated PR head's tree, as a squash merge of an up-to-date branch
does; otherwise the workflow exits green without promoting, and the change
must be re-gated.
