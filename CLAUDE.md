# CLAUDE.md — pineforge-codegen

> Project memory for AI coding agents. Keep terse and concrete. When the
> codebase invariants below are wrong, the truth is the test suite —
> update this file alongside any change that would break one of these
> claims.
>
> `AGENTS.md` is this file's harness-neutral twin (Codex, OpenCode and other
> agents read it): keep the two in step. Only its last section, the parity
> campaign gate that a harness without Claude Code's hooks must run by hand,
> is its own.

## REQUIRED before claiming any change is done

On the final candidate, once, before opening a PR, marking a task complete, or telling the user the change is ready (the commands are under "Testing"):

1. **Every diff:** the tests that touch your change pass and the PR's CI is green. The required check is `gate`.
2. **Native PR changes:** the required `engine-main` prerequisite runs engine-enabled touched tests plus unchanged construction/compile/checked-settings smoke against freshly built ENGINE MAIN. The trusted base selector decides native work; only a successful identity-bound non-native decision permits its visible job-level skip. See `docs/engine-main-ci.md`.
3. **A merged diff under `pineforge_codegen/`:** run the full engine-enabled suite once on the final `main` tree, corpus gate (`tests/test_compile_corpus.py`) included. Inspect skip reasons: no compile or end-to-end test may skip for want of a public native input. Non-public inputs may skip. This full-main run is an operator obligation on an approved worker; the PR touched run and existing pure-Python matrix do not replace it.
4. **Every main push:** `engine-main-touched.yml` always builds ENGINE MAIN and runs native touched+smoke, even for docs changes, on the exact event after SHA with before/after binding. No path filter or cancellation group may drop a main event. Its normal job check belongs to that pushed SHA. Historical dispatch uses the isolated native check-run publisher; a definition-commit check is not evidence on the historical source.

These CI requirements describe this source candidate after staged adoption.
Source, unit results and documentation do not establish installation or hosted
proof; ROOT/CORE retain publication and verification ownership.

If the corpus gate newly fails, fix it before doing anything else. Adding a strategy to `KNOWN_TRANSPILE_FAILURES` / `KNOWN_COMPILE_FAILURES` to silence a corpus failure is only acceptable when the failure represents an intentional, documented support drop — and even then, only with a matching one-line rationale next to the entry.

A pytest run without a resolvable engine remains useful during development, but cannot establish native coverage. PR validation uses touched+smoke; full-main verification uses the engine-enabled suite and its skip reasons. Source inspection alone proves neither.

## What this is

- PineScript v6 → C++ transpiler, pure Python, no runtime dependencies. Entry points: `pineforge_codegen.transpile()` and `transpile_full()`; the Pyodide package ships the `gate/glue.py` JSON protocol (`docs/PUBLIC_CONTRACT.md`).
- Source-available (PineForge Source License 1.2, `LICENSE`). The emitted C++ links against the Apache-2.0 runtime `pineforge-engine`, a sibling repo, usually `../pineforge-engine`.
- Pairing: from 1.0.0 on a released codegen `X.Y.Z` pairs only with engine `vX.Y.Z` (prereleases exactly); the last 0.x release, codegen 0.10.4, pairs with engine `v0.13.1`. Use that release's generated headers and `libpineforge.a`; regenerate C++ and relink on every pair change; equal `PF_ABI_VERSION` is insufficient. Pair table: README.md and CONTRIBUTING.md, "Engine pairing".
- Corpus: the engine's `corpus` submodule holds the Pine v6 strategies every change must still transpile and compile (`tests/test_compile_corpus.py`).

## Build and environment

Python >= 3.11 (CI runs 3.11, 3.12, 3.13 and 3.14, with `PYTHONHASHSEED=0`); Node 22 only for the Pyodide gate.

```bash
python -m pip install -e ".[dev]"   # pytest and pytest-xdist
npm ci                              # Pyodide gate only (gate/, package.json)
```

Native inputs unlock the local-only tests ("Testing"). The paths assume the engine is built in `../pineforge-engine/build` (CONTRIBUTING.md "Local setup" has the CMake commands). **Rebuild the engine first if any of its C++ headers or sources changed.** Explicit paths make coverage reproducible even if a sibling checkout is auto-detected; `CXX` picks the compiler (default `g++`).

```bash
export PINEFORGE_ENGINE_INCLUDE=../pineforge-engine/include
# Point Eigen at a system include tree or CMake's fetched source:
export PINEFORGE_EIGEN_INCLUDE=../pineforge-engine/build/_deps/eigen-src
# The generated version header, built runtime and corpus unlock their tests:
export PINEFORGE_GENERATED_INCLUDE=../pineforge-engine/build/include
export PINEFORGE_ENGINE_LIB=../pineforge-engine/build/lib/libpineforge.a
export PINEFORGE_ENGINE_CORPUS=../pineforge-engine/corpus
git -C ../pineforge-engine submodule update --init corpus   # once
git -C ../pineforge-engine/corpus lfs pull                  # once: the 1m feed is Git LFS
```

With an engine resolved but its corpus not initialised, `tests/test_parser.py` fails (`corpus-missing`) instead of skipping.

## Layout

- `pineforge_codegen/` — the transpiler: lexer, parser, support checker, `analyzer/`, `codegen/`.
- `tests/` — pytest suite (`testpaths = ["tests"]`). `_compile.py` and `_e2e.py` are the harnesses that skip cleanly without native inputs; data lives in `fixtures/`, `golden/`, `gate-corpus/`.
- `gate/` — Pyodide differential gate (`run-gate.mjs`, `selftest.mjs`; `glue.py` is the canonical copy of the JSON protocol). `scripts/` — generators and release helpers. `npm/` — npm package assembly.
- `docs/` — `PUBLIC_CONTRACT.md`, `ci.md` (CI admission and check mapping), `reference/`. `.github/workflows/` — CI. `CHANGELOG.md` — update it for user-visible changes.

Reference sections, moved out of this file byte for byte; read the one your change needs:

- `docs/reference/pipeline.md` — Pipeline: the six passes of `transpile()`, in order.
- `docs/reference/file-map.md` — full file map: every module of `pineforge_codegen/` and `tests/`, one line each.
- `docs/reference/support-contracts.md` — Support contracts: what the support checker refuses or lowers, plus the contract tables (`request.security`, array history, epoch ints, ...); tests cite its rows.
- `docs/reference/known-codegen-quirks.md` — Known codegen quirks, read before changing emission; tests cite the quirk numbers.
- `docs/reference/adding-a-pine-function.md` — How to add a new Pine v6 function, with a worked example.
- `docs/reference/conventions.md` — Conventions, the long rules: types, errors, escapes, reserved names, input limits, ...

## Testing

Sources of truth: `.github/workflows/` and `docs/ci.md` for CI; `tests/_compile.py` and `tests/_e2e.py` for skips. When this section and those files disagree, they win.

### What CI runs

| Trigger | Workflow (file) | Jobs and what they check |
| --- | --- | --- |
| Pull request to `main` | `GKE PR` (`gke-pr.yml`) | `admit` (identity and workflow admission), `workflow-lint`, `package-files` (`python -m pytest -q tests/test_license_entity.py`), `pytest` (`python -m pytest -q -n auto`, Python 3.11 to 3.14), `differential` (Pyodide parity: selftest, audit, full gate), `engine-main` (trusted changes decision and conditional engine-enabled touched+smoke via `engine-main-touched.yml`), `test-check`, `result` |
| Push to `main`, manual dispatch | `Test` (`test.yml`) | `authz`, `changes`, `workflow-lint`, `package-files`, `pytest` (same command and versions), `test-check` |
| Every push to `main`, authorized proof dispatch | `Engine main touched` (`engine-main-touched.yml`) | `changes`, `native` (always on main; touched+smoke), `publish` (dispatch-only historical check on tested SHA; no `gate` status) |
| Push to `main`, manual dispatch | `promote-baseline` (`promote-baseline.yml`) | `promote-guard`, `promote`: baseline promotion after a merge; runs no tests |
| Schedule `27 5 * * *`, manual dispatch | `Pyodide Gate` (`gate.yml`) | `preflight-docs`, `differential`, `pytest` (`python -m pytest -q`, serial), `gate` |
| Manual dispatch only | `Release` (`release.yml`), `Publish codegen-pyodide` (`publish-pyodide.yml`) | the Pyodide gate before anything is built or published (jobs `release`, `publish`); no pytest. A tag push alone runs nothing |

**Required check: `gate`.** The existing `result` job of `gke-pr.yml` is its sole status writer. It needs all seven prerequisites, including `engine-main`; all must succeed. It also checks the reusable workflow's actual changes/native results and bound identities/digest: native changes require execution success, while a genuine non-native decision requires a visible native job-level skip. All previous checks still run on docs-only diffs. Fork/draft PRs remain unadmitted. The scheduled `Pyodide Gate` is not this PR status. Workflow/checker changes require matching admission controls and staged base adoption (`docs/ci.md`).

The existing pytest matrix still exports no native inputs, so its engine-dependent tests skip. The new PR prerequisite and every-main-push native route supply real headers, generated header, archive, Eigen and compiler before importing selected tests; skip/xfail there fails. Candidate execution has only read permission. Only the isolated dispatch publisher gets checks-write permission and binds the actual completed job before publishing on its tested SHA. ENGINE MAIN integration does not establish release pairing. The full engine-enabled suite/corpus still runs once on main on an approved worker. Read actual pytest summaries for skips. Run the local Pyodide gate only for `gate/` changes; CI retains it on every PR.

### Engine-enabled tests (selected cases now run in native PR CI)

They need the native inputs from "Build and environment". Without them they skip, and a skip is not a pass. Run one with `python -m pytest -q <file>::<test>` (or `-k <pattern>`); add `-ra` to print every skip reason.

| Tests | Needs (skip reason when missing) | Run when your diff touches |
| --- | --- | --- |
| Corpus gate: `tests/test_compile_corpus.py` (`python -m pytest -ra tests/test_compile_corpus.py`) | compiler, engine include, Eigen and the corpus (`set PINEFORGE_ENGINE_INCLUDE plus a valid pineforge-engine corpus checkout`) | anything under `pineforge_codegen/` (invariant 5) |
| Corpus readers, no compiler needed: the corpus tests of `tests/test_parser.py`, `tests/test_codegen_golden.py`, `tests/test_checked_settings.py`, `tests/test_input_metadata_receipt.py`, `tests/test_method_call_receiver.py` | the engine checkout and its corpus (`no engine checkout`, `engine-corpus source not available`, `PINEFORGE_ENGINE_CORPUS is needed`, `the engine corpus is unavailable`) | the lexer, parser, input manifest, checked settings, or the C++ emitted for corpus-style scripts |
| Compile-only: `tests/test_compile_smoke.py`, `tests/test_na_int_narrowing.py` and every file calling `compile_cpp` or `skip_if_no_compile_env` | a C++ compiler (`CXX compiler not found on PATH`), `PINEFORGE_ENGINE_INCLUDE` (`not set or invalid`), Eigen (`Eigen3 headers not found`); `pineforge/version.h` is found or synthesized | emitted C++: `pineforge_codegen/codegen/`, `analyzer/`, `signatures.py`, `support_checker.py` |
| Run the emitted translation unit: files calling `run_emitted_tu` or `skip_if_no_engine_lib` (e.g. `tests/test_single_line_body_scope.py`) | also the built runtime (`No built libpineforge.a`) | what emitted code does at run time: TA, series, na and int narrowing, collections |
| End-to-end replays: `tests/test_e2e_*.py` and every file calling `skip_unless_e2e_env` | also the engine's `scripts/run_strategy.py` and the Git LFS 1m feed `corpus/data/ohlcv_ETH-USDT-USDT_1m.csv` (`corpus 1m feed missing or an unsmudged LFS pointer`) | the behaviour a replay pins; the topic is in the file name |
| Against an earlier commit: files calling `reference_codegen` | git history, not a shallow clone (`is not in this checkout's history`), plus the end-to-end inputs | their end-to-end topic |
| `tests/test_host_member_names.py` | `clang++` (`needs clang's AST dump`) | a lowering starts reading a host member, or the engine's host header changes |
| Non-public inputs: files calling `private_fixture`, `tests/test_e2e_series_timeframe_values.py` (`PINEFORGE_BTCUSDT_1H_FEED`), the population test of `tests/test_string_settings_constants.py` (`PINEFORGE_STRING_SETTINGS_SOURCES`) | inputs outside this repository (`private fixtures not present:`) | cannot run here: they skip; say so in the PR |

Full-main verification, once per final merged product tree on an approved worker (native inputs exported):

```bash
python -m pytest -q -ra -n auto                     # the whole suite, with skip reasons
python -m pytest -ra tests/test_compile_corpus.py   # the corpus gate alone, a subset: for iterating, not a rerun
```

PRs run the tests touching the change plus required native touched+smoke when selected. The full-main obligation is separate; do not silently turn unrelated old CI jobs into native full-suite jobs.

### Rules that keep verification short

- While you work, run only the tests that touch your change: `python -m pytest -q <file>::<test>` or `-k <pattern>`; find them by searching `tests/` for the symbol you changed.
- The existing PR matrix covers the whole pure-Python suite; native PR CI is touched+smoke. The full engine-enabled suite runs once on the final main tree. Do not rerun a suite already green on the same tree.
- Every test has one specific target (one behaviour or defect). Tests must not duplicate each other: tests with the same target are merged in a separate reviewed change. Add a new test only for a target no existing test covers. Never delete or skip a test whose target is unique.
- No extra verification layers on top of the repo's gates: no home-made kits, receipt packs, second reads of the same suite, or reruns.
- `python -m pytest --collect-only -q` gives today's collection size; never quote a fixed count.

### What a script computes is a parity probe, not a CI test

- A check of what a Pine script computes (script plus data gives expected values or trades) is a **probe** in the parity campaign's population, not a test in this suite. Crypto data goes to the public corpus; any other data goes to the private set. The campaign's parity sweep measures it against TradingView.
- The evidence for a behaviour fix is a probe in that population, measured by a sweep, not a new test here. A new test here is a real unit test: one target (the parser, the analyzer, a diagnostic, the shape of emitted code), no duplicates.
- Migration: an existing test leaves this suite only after its probe is admitted and measured in a parity sweep. Nothing is removed before that; until then the test stays and the engine-enabled list above still applies to it.
- Engine-dependent tests skip in the old pytest matrix. Count native PR coverage only for cases actually selected and executed by `engine-main`; disclose non-public exclusions and propose their probes.
- The engine's own kernel tests (ctest, in the engine repository) are a separate class: they stay in the engine's CI, one target each, and are never probe candidates.

## Architectural invariants — do not break

1. **Exact engine release pairing.** From 1.0.0 on, bumping `VERSION`
   requires the matching `pineforge-engine` tag (`X.Y.Z` with `vX.Y.Z`;
   prereleases exactly); the 0.x releases pair as "What this is" states.
   Use that tag's generated headers and `libpineforge.a`; regenerate every
   strategy TU and relink on a pair change. `PF_ABI_VERSION` equality alone is
   insufficient. The transpiler does not ship a runtime artifact.
2. **Pure-Python, zero runtime deps.** `pyproject.toml::dependencies = []`.
  Do not introduce a runtime dependency. `dev` extras are test tools only (pytest, pytest-xdist).
3. **Compile tests need an engine checkout and Eigen.** The harness
   (`tests/_compile.py`) uses `PINEFORGE_ENGINE_INCLUDE` or a resolvable sibling
   checkout. It skips when the engine or Eigen is absent; runtime tests also
   need a built `libpineforge.a`. Native PR CI explicitly supplies ENGINE MAIN
   inputs; this forward integration does not replace exact release pairing.
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
   mandatory part of the full-main run for every product change, because
   parser, lexer, support-checker, and signature tweaks can also break corpus
   strategies via second-order effects. PR native scope stays touched+smoke;
   it initializes the public corpus only when a selected case needs it.
   If you intentionally drop a Pine construct, add the strategy to
   `KNOWN_TRANSPILE_FAILURES` / `KNOWN_COMPILE_FAILURES` with a
   one-line rationale.

6. **Deterministic emission.** Node `id()` values are lookup keys, never
   ordering keys. Requested expression-history resets follow registration
   order; TA variants follow binding source positions, then stable scope
   snapshots, retaining traversal order at ties. Inline TA identity keys are
   an unordered membership set.

## Emission invariant

Emit C++ only for a form the analyzer knows how to lower correctly.
Never emit a placeholder, an empty expression, an undeclared
identifier, or a C++ operator or conversion that does not exist.

A change must not make a script that TradingView accepts, and that
transpiles and compiles today, fail to transpile. This non-regression rule
takes precedence over adding refusals for missing or incorrect lowerings,
including emitter fallbacks. Refuse only a form TradingView refuses, or a
form that already fails the C++ compile. When the lowering of a
TradingView-valid form is approximate or wrong, implement its semantics;
until then it keeps compiling and is recorded as a defect, never newly
refused as unproven.

For a form eligible for refusal under this rule, diagnose a missing lowering
at translation time, before it reaches the emitter, with a `PF-E` diagnostic
at its Pine line and column and a message naming the construct.
Give every emission path either a correct lowering or an analyzer refusal
permitted by this rule in front of it. An emitter fallback must raise an
internal error that the translator turns into a located `PF-E` refusal;
it must never print a placeholder such as `/* unknown */`. Bypassing the
support checker does not waive this invariant.

Match TradingView's Pine v6 grammar and typing. Refuse a form TradingView
refuses through one general grammar or typing rule, not a list of shapes.
Emit correct C++ for a form TradingView accepts; refusing it is a support
gap to fix. Fix construct classes generically: no feature flags, per-script,
per-id or per-literal special cases, or shape lists where a signature or
grammar rule exists.

Treat any generated-C++ compile failure as a P0 defect. When TradingView
refuses a form and PineForge instead fails in g++, the matching outcome
is still a P0 defect, not a valid translation-time refusal.

The notes in `docs/reference/` and `docs/codegen-coverage-gaps.md` describe
existing behavior. Documented approximations and unsupported fallbacks there
are implementation gaps, not exceptions to this invariant.

## Conventions

Quoted from `docs/reference/conventions.md`, which holds the rest.

- **Type system.** `PineType` (in `symbols.py`) is intentionally small and aligns with TradingView's primitive types. For collection / composite types use `TypeSpec`.
- **Type strings.** Codegen's `_infer_type` returns a C++ type STRING (e.g. `"std::string"`, `"PineMatrix"`) — that's what most emission paths consume.
- **Errors.** Use `errors.CompileError` for fatal issues raised from the transpiler. Carry `SourceLocation` so users can map back to the Pine line/col.
- **Stubs.** A comment cannot substitute for a lowering. Apply the [emission invariant](#emission-invariant) instead of emitting an unsupported stub or leaving a failure for the C++ compiler.
- **Escapes.** A string a script spells reaches the C++ only as a string literal's value (`_cpp_string_escape`) or as comment text (`_cpp_comment_escape`): escape every script-derived value where it is written into a literal or a comment, a validated one too (`tests/test_emitted_string_literals.py`).
- **Helper underscores.** Codegen-internal helpers in `codegen/tables.py` are underscore-prefixed (`_matrix_add_row`, `_merge_kwargs`); they are not part of the package's external surface.
- **Reserved names.** `codegen/helpers.py::CPP_RESERVED` carries every C++17 and C++20 keyword/operator alternative plus header macros and emitter names that can collide.
- **Host members** (`codegen/host_members.py::HOST_MEMBER_NAMES`). Never edit that set by hand: `scripts/gen_host_members.py` derives it from clang's AST of the host header the emitted C++ includes and the identifiers the emitter's string constants spell, and `tests/test_host_member_names.py` regenerates it and checks it against every host member a transpiled battery names.
- **Emitter temporaries:** a new template local belongs in the `_pf_` namespace (`tests/test_e2e_temporary_name_hygiene.py` derives the set from emitted C++).
- **Input limits.** `limits.py` turns a crash or a hang into a located `CompileError`; where TradingView documents a limit, ours is at least as large.
- **Budget checks.** A per-item loop whose body scans the whole script needs its own `self._budget.check` (the `var`-member scans in `codegen/base.py`)
- **Call paths.** A walk that follows user calls must not visit a helper once per call path: a diamond of helpers (`f1(x) => f0(x) + f0(x)`, 22 levels) has 2**22 paths.

## Safety rules for AI agents working in this repo

- **Never silently widen `SUPPORTED_*`.** Every addition needs a
matching entry in the per-namespace official set in
`tests/test_official_surface.py`, or it breaks the surface lock-in.
- **Never delete a `test_regression_*` case** without first
understanding which once-broken codepath it pins. The xfail->pass
history is intentional.
- **Native PR changes and every main push require engine-enabled touched+smoke; product changes also require full engine-enabled verification once on main**, including the corpus gate. A corpus or compile-smoke failure is a regression; inspect skips to confirm the C++ paths ran.
- **Don't update `VERSION`** (from 1.0.0 on) without confirming the exact
  matching engine tag and its generated headers and static library are
  available.
- **Don't introduce runtime dependencies.** Pure-Python is the install
contract. Test extras (pytest, pytest-xdist) are the only allowed `[project.optional-dependencies]`.
