# Translate implies compile in PR CI

The Test workflow's existing Python 3.11–3.14 matrix sets
`PINEFORGE_REQUIRE_COMPILE=1`. Missing g++, engine headers, or Eigen now fails
the compile helper, including its collection-time availability guard. Local
runs without that flag retain the existing opt-in skips.

The job checks out `pineforge-4pass/pineforge-engine` at `main`, as
CONTRIBUTING.md specifies for codegen development, and initializes the engine's
pinned corpus submodule. It uses the existing read-only Actions token, disables
credential persistence, and installs Ubuntu's `g++` and `libeigen3-dev`.
`tests/_compile.py` already resolves or synthesizes the generated version
header from that engine source tree; a runtime build is unnecessary for a
syntax-only check. The checkout SHA and submodule state appear in the job log.
Released codegen still requires its exact matching engine release; this
development job does not establish a release pair.

`tests/test_translate_implies_compile.py` recursively discovers every `.pine`
under `tests/fixtures` and `tests/gate-corpus`, including community repros.
It supplies the repository's synthetic library sources to importing fixtures.
A second sweep recursively discovers the paired engine corpus's
`strategy.pine` files, including the deeper directories the existing shallow
corpus test misses. A missing or empty corpus fails required CI.

Only sources for which `transpile()` returns C++ enter the compile check.
`CompileError` rejections, including expected-error fixtures and standalone
library sources, are listed separately and receive no compiled credit.
Other front-end exceptions fail the test. The batch keeps compiling after
compiler errors and asserts only after collecting every result.

Each matrix job handles one of four deterministic shards with at most two
concurrent syntax-only compilers; dedicated known-failure rows run in every job.
`KNOWN_COMPILE_FAILURES` maps exact repository paths to compiler-failure reasons.
Its sole entry is `tests/fixtures/drawing_lifetime/w9dg-label-collect/strategy.pine`.
Both ordinary batches exclude registry paths; dedicated rows still compile them.
Registry rows and #281 cases 2–3 strictly xfail only on `FixtureCompileFailure`.
Case 1 of #281 strictly xfails only on `CompileError`: the front end rejects valid Pine.
Registry controls check real files, disjoint batches, exact marks and full coverage.
A successful row is strict XPASS, forcing removal of its marker or registry entry.
The generated preflight strategy must compile before any xfail body can run.

Each job uploads `compile-report-python-<version>` even when pytest fails:

- `compile-failures.txt`: one path and first compiler error line per failure,
  including the registered failure and #281 cases 2–3.
- `front-end-rejections.txt`: sources rejected before producing C++.
- `results.json`: per-source outcomes, compiler seconds, shard identity and
  sweep wall time.

The unchanged required `test-check` depends on the full pytest matrix.
An unexpected compile failure, strict XPASS, missing required input, or failed
checkout therefore prevents that check from succeeding.

On a CLOUD checkout with the paired compile environment already exported,
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

There are 526 repository Pine fixtures after adding the three repros. At a
provisional two seconds per syntax-only compile, four shards and two compilers
per shard imply about 2.3 minutes per job for those fixtures, plus about
1.4 minutes for a 325-strategy engine corpus. The only repository timing hint
is the small-fixture `<2s` comment in `tests/test_compile_smoke.py`; it is not a
measurement on GitHub's runner. Checkout, package installation, unequal source
sizes and newly enabled existing compile tests add unmeasured time. The
existing slow-compiler control deliberately adds about 65 seconds per matrix
job once compile inputs are present. Use the uploaded timings and Actions
job durations to replace this estimate after the first run.

Private access remains an owner decision if either checkout is unavailable.
The repository's existing App-token jobs use protected `release` or
`release-bot` environments. No App key is exposed to the PR pytest jobs here.
One option is an approved protected acquisition job with a short-lived token
limited to contents-read on the engine and corpus, exporting source artifacts
to the matrix; this needs confirmed installation access and environment
policy. Another is a public, versioned header/corpus artifact, which needs a
publisher and integrity/version metadata. No new secret, permission or pin
file is introduced by this change.

This change edits only the Test workflow. The separate Pyodide Gate and
Release workflows still need their own compile dependency; their workflow
files were outside this lane's edit allowlist. Header-only CI also does not
replace the engine-enabled runtime and corpus validation required by
AGENTS.md and CONTRIBUTING.md before completion.
