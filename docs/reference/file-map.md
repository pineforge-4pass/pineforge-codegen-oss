## File map

```
pineforge_codegen/
├── lexer.py / tokens.py            Token stream
├── parser.py / ast_nodes.py        Pine v6 AST
├── limits.py                      Located source/complexity/time budgets
├── builtin_keywords.py             nz / fixnan keyword arguments rewritten
│                                   to their positions after the support
│                                   check (TradingView's parameter order)
├── loop_types.py                   Counted-loop counter-family evidence,
│                                   shared by analyzer and emitter, with a
│                                   memo per declaration in each query;
│                                   its value query alone sees fractional
│                                   reassignment storage before analysis;
│                                   check types retain hints or initializers.
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
├── diagnostic_codes.py             Stable diagnostic codes: a Diagnostic's
│                                   code/args are read off its text by the
│                                   ICU templates of diagnostics_catalog.json
│                                   (scripts/gen_diagnostics_catalog.py adds a
│                                   code per new template; the pin in
│                                   tests/fixtures/diagnostic_codes_pin.json
│                                   never changes a code's meaning).
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

