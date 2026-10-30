# Translate implies compile in PR CI

The four Python 3.11–3.14 legs stay unchanged from main.
The `compile` job uses Python 3.12 and has eight parts:
four sweep shards (`sweep-0`, `sweep-1`, `sweep-2`, `sweep-3`) and
four suite slices (`suite-0`, `suite-1`, `suite-2`, `suite-3`).
All eight parts set `PINEFORGE_REQUIRE_COMPILE=1`.
Each sweep shard runs the sweep, compile-helper and workflow-contract tests.
The suite slices partition the remaining test files once across four parts.
All eight compile parts must be green.
The required aggregate job name remains `compile`.
A re-run must re-run all of `compile`.
Repository required checks should list `test-check`, which aggregates `pytest` and `compile`.
If settings list matrix legs individually, require all eight compile parts as well.
A measured sweep shard took 239 s of wall time and compiled 196 scripts.
Suite slice times are measured by the first run.

`tests/_compile_partition.py` reads `testpaths` and `python_files` from
`[tool.pytest.ini_options]` in `pyproject.toml`.
The current settings select `test_*.py` recursively under `tests`; `*_test.py`
is not a collection pattern here.
For slice K, it excludes the three sweep-run files, sorts the remaining
repository-relative paths, and selects the files at indices K modulo 4.
The excluded files are `tests/test_translate_implies_compile.py`,
`tests/test_require_compile.py`, and `tests/test_ci_compile_contract.py`.
The workflow calls this helper with its matrix slice index, saves the printed
paths under `RUNNER_TEMP`, and passes those paths as pytest arguments.
An empty slice fails before pytest can fall back to collecting the full suite.
The contract test imports the same slice function and checks the real file
inventory for complete coverage, non-empty slices, and no overlap.
Mutation controls cover a missing matrix part, a changed stride, missing sweep
exclusions, and an empty slice. No dependency or pytest plugin is added.

Missing g++, engine headers, or Eigen fails the compile helper, including its
collection-time availability guard. Local runs without the flag retain the
existing opt-in skips.

The compile job checks out `pineforge-4pass/pineforge-engine` at `main`, as
CONTRIBUTING.md specifies for codegen development, and initializes the engine's
pinned corpus submodule. It uses the existing read-only Actions token, disables
credential persistence, and installs Ubuntu's `g++` and `libeigen3-dev`.
`tests/_compile.py` already resolves or synthesizes the generated version
header from that engine source tree; a runtime build is unnecessary for a
syntax-only check. The checkout SHA and submodule state appear in the job log.
Released codegen still requires its exact matching engine release; this
development job does not establish a release pair.

Pairing each PR with engine `main` is by design. Recorded engine and corpus SHAs
identify the engine and corpus used by each result. The first result that counts
is the one on the final engine `main`.

The sweeps cover every `.pine` under `tests/fixtures` and `tests/gate-corpus`,
plus every recursive `strategy.pine` in the paired engine corpus.
Tests that transpile inline strings elsewhere are not swept.
`tests/test_translate_implies_compile.py` includes the community repros.
It supplies the repository's synthetic library sources to importing fixtures.
The engine sweep includes the deeper directories the existing shallow corpus
test misses. A missing or empty corpus fails required CI; the full corpus list
must contain at least 312 strategies, checked before sharding.

Only sources for which `transpile()` returns C++ enter the compile check.
Ordinary-sweep `CompileError` rejections, including expected-error fixtures and
standalone library sources, are listed separately and receive no compiled credit.
Other front-end exceptions fail the test. The batch keeps compiling after
compiler errors and asserts only after collecting every result.

The ordinary sweeps use `tests/front_end_rejection_baseline.txt` as a ratchet.
A new rejection fails, as does a listed script in that shard that stops rejecting.
Remove an entry when its script starts translating; add an entry only to record
an explicitly reviewed rejection, using its exact report label in sorted order.
Engine labels start with `engine-corpus/`; entries do not claim rejected scripts are wrong.

Each sweep part handles one of four deterministic shards with at most two
concurrent syntax-only compilers; dedicated known-failure rows run in every
sweep part.
`KNOWN_COMPILE_FAILURES` maps exact repository paths to compiler-failure reasons.
Its sole entry is `tests/fixtures/drawing_lifetime/w9dg-label-collect/strategy.pine`.
Both ordinary batches exclude registry paths; dedicated rows still compile them.
Registry rows and #281 cases 2–3 strictly xfail only on `FixtureCompileFailure`.
Case 1 of #281 strictly xfails only on `CompileError`: the front end rejects valid Pine.
Registry controls check real files, disjoint batches, exact marks and full coverage.
A successful row is strict XPASS, forcing removal of its marker or registry entry.
The generated preflight strategy must compile before any xfail body can run.

Each sweep part uploads `compile-report-<part>` even when pytest fails;
missing sweep reports fail the upload. Each suite slice also attempts an upload
with that name, but permits an empty report directory because it excludes the
sweep module that writes these files:

- `compile-failures.txt`: one path and first compiler error line per failure,
  including the registered failure and #281 cases 2–3.
- `front-end-rejections.txt`: sources rejected before producing C++.
- `results.json`: per-source outcomes, compiler seconds, shard identity,
  sweep wall time, `engine_sha` and `corpus_sha`.
- `pairing.txt`: one line with `PINEFORGE_ENGINE_SHA` and `PINEFORGE_CORPUS_SHA`.

The prerequisite step exports both checkout SHAs through `GITHUB_ENV`.
With `PINEFORGE_REQUIRE_COMPILE=1`, a missing SHA or a value other than 40 hex
characters fails sweep setup. Optional runs record `unrecorded` for an unavailable
or malformed SHA.

The required `test-check` depends on the full pytest and compile matrices.
An unexpected compile failure, strict XPASS, missing required input, or failed
checkout therefore prevents that check from succeeding. A skipped compile job
is accepted only with the aggregate's independent proof of a cheap PR diff,
using the same rule as a skipped pytest job.

On a CLOUD checkout with the paired compile environment already exported,
also export `PINEFORGE_ENGINE_SHA` and `PINEFORGE_CORPUS_SHA` from the engine
and corpus checkouts' respective `git rev-parse HEAD` results. Then
these commands expose #281 without its markers and then exercise its strict
expected failures. Run them as separate verification rows; the first is
intentionally red before the CGI fixes:

```bash
PINEFORGE_REQUIRE_COMPILE=1 python -m pytest -ra --runxfail tests/test_translate_implies_compile.py -k issue_281
```

```bash
PINEFORGE_REQUIRE_COMPILE=1 python -m pytest -ra tests/test_translate_implies_compile.py -k issue_281
```

For the complete unsharded check, use a fresh report directory, set
`PINEFORGE_COMPILE_SHARD=0` and `PINEFORGE_COMPILE_SHARDS=1`, then run:

```bash
PINEFORGE_REQUIRE_COMPILE=1 python -m pytest -ra tests/test_require_compile.py tests/test_ci_compile_contract.py tests/test_translate_implies_compile.py
```

Use uploaded sweep timings and Actions job durations from the first run to
measure each part, including all four suite slices.

Private access remains an owner decision if either checkout is unavailable.
The repository's existing App-token jobs use protected `release` or
`release-bot` environments. No App key is exposed to the PR test jobs here.
One option is an approved protected acquisition job with a short-lived token
limited to contents-read on the engine and corpus, exporting source artifacts
to the matrix; this needs confirmed installation access and environment
policy. Another is a public, versioned header/corpus artifact, which needs a
publisher and integrity/version metadata. No new secret, permission or pin
file is introduced by this change.

Of the CI workflows, only Test is changed. The separate Pyodide Gate and
Release workflows still need their own compile dependency; their workflow
files were outside this lane's edit allowlist. Header-only CI also does not
replace the engine-enabled runtime and corpus validation required by
AGENTS.md and CONTRIBUTING.md before completion.
