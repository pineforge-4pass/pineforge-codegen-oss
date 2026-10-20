# Contributing to pineforge-codegen

This is the source-available PineScript v6 to C++ translator. Read the
[license and contribution terms](LEGAL.md) before sending a material change.
Contributions use the Developer Certificate of Origin: add a `Signed-off-by`
line with `git commit -s`. Material contributions also require a Contributor
License Agreement so pineforge, LLC can include them in its commercial licenses;
contact luis@4pass.com.tw before opening a material pull request.

## Local setup

Use Python 3.11 or newer, Node 22, a C++17 compiler, CMake, and a matching
[`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine)
checkout. Clone codegen and engine as siblings, then install the development
dependencies from this repository:

```bash
python -m pip install -e '.[dev]'
npm ci
```

Build the paired engine outside its source checkout if you need compile and
runtime tests. This example enables the source layer (the default) and limits
the build to four jobs:

```bash
cmake -S ../pineforge-engine -B /tmp/pineforge-engine-build \
  -DCMAKE_BUILD_TYPE=Release -DPINEFORGE_BUILD_TESTS=OFF \
  -DPINEFORGE_BUILD_SOURCE_LAYER=ON
cmake --build /tmp/pineforge-engine-build -j4
```

If CMake fetches Eigen, its headers are under the build tree's
`_deps/eigen-src`; a system Eigen install is also fine. The engine corpus is
the engine's `corpus` submodule
([`pineforge-corpus`](https://github.com/pineforge-4pass/pineforge-corpus)),
and the 1m feed the E2E tests replay is stored in Git LFS:

```bash
git -C ../pineforge-engine submodule update --init corpus
git -C ../pineforge-engine/corpus lfs pull
```

The corpus test skips when it cannot find that tree, and the E2E tests skip
when the feed is missing or still an LFS pointer, so inspect skip reasons
before treating a run as complete.

## Engine pairing

Codegen `main` is developed and tested against engine `main`. Codegen 1.3.0
pairs with engine `v1.3.0`, the pair the
[`pineforge-release`](https://github.com/pineforge-4pass/pineforge-release)
image `1.3.0` ships, codegen 1.2.0 with engine `v1.2.0`, the pair of the image
`1.2.0`, codegen 1.1.0 with engine `v1.1.0`, the pair of the image `1.1.0`,
codegen 1.0.1 with engine `v1.0.1`, the pair of the image `1.0.1`, and codegen
1.0.0 with engine `v1.0.0`, the pair of the image `1.0.0`. On the
0.x line the two version lineages are independent:
the last 0.x release, codegen 0.10.4, pairs with engine `v0.13.1`, the pair
the `pineforge-release` image `0.1.25` ships.

From 1.0.0 on, a released codegen `X.Y.Z` is supported only with engine tag
`vX.Y.Z`, using that release's generated headers and `libpineforge.a`.
Prereleases match exactly too: codegen `1.0.0-rc.1` requires engine
`v1.0.0-rc.1`. Do not pair different patch or prerelease tags, even if
`PF_ABI_VERSION` is equal.

On **every** pair change, rerun codegen on the Pine source, then rebuild and
relink each generated strategy translation unit against the new pair's headers
and static library. Replacing only the library, or recompiling old generated
C++, is not a supported migration. Development branches can test against
paired in-progress engine commits, but that does not create a supported
cross-version release pair. See the [public contract](docs/PUBLIC_CONTRACT.md).

## Required checks

Point the test harness at the engine source, its generated header, runtime
archive, Eigen headers, and public corpus. Adjust these paths for your build:

```bash
export PINEFORGE_ENGINE_INCLUDE=../pineforge-engine/include
export PINEFORGE_GENERATED_INCLUDE=/tmp/pineforge-engine-build/include
export PINEFORGE_ENGINE_LIB=/tmp/pineforge-engine-build/lib/libpineforge.a
export PINEFORGE_EIGEN_INCLUDE=/tmp/pineforge-engine-build/_deps/eigen-src
export PINEFORGE_ENGINE_CORPUS=../pineforge-engine/corpus
export CXX=/usr/bin/c++
python -m pytest -ra
python -m pytest -ra tests/test_compile_corpus.py
```

The full suite includes the corpus sweep; run the corpus command separately so
its coverage and result are visible. A green suite with compile or E2E skips
does not verify generated C++ against the engine. If you change engine C++
headers or sources, rebuild the engine before rerunning these checks. The
current item count is determined by `python -m pytest --collect-only -q`, not
by a fixed number in this guide.

Check the Pyodide package and native/Pyodide parity as well:

```bash
npm ci
npm audit --audit-level=high
npm run gate:selftest
GATE_FULL=1 node gate/run-gate.mjs
```

The full gate writes `release.json` and the validated archive consumed by
`node scripts/build-npm-package.mjs`. For release packaging checks, install
the Python `build` frontend, run `python -m build`, assemble the npm payload,
then run `npm pack --dry-run` from `npm/`. These commands build local artifacts;
publishing is a separate release operation.

## Change and review notes

- Add or update tests for behavior changes. Keep the engine corpus compiling;
  an intentional support drop needs the rationale required by [AGENTS.md](AGENTS.md).
- If existing scripts emit different C++, list the affected public-corpus
  translation units and whether their trades move when run with the matching
  engine.
- Keep [CHANGELOG.md](CHANGELOG.md) current for user-visible changes. Its
  release-note policy applies to both stable and prerelease tags.
- Each CI workflow starts with the org's canonical `authz` job, with no
  checkout, secrets or token scopes, and fails red unless the run belongs
  to us. A same-repository PR must have a non-fork head.
  The author check accepts an owner, member or collaborator, or either
  allowlisted bot: `dependabot[bot]` and `pineforge-app[bot]`.
  For any other author, including a `CONTRIBUTOR` with private org
  membership, GitHub must report `admin`, `maintain` or `write` permission
  on this repository; a failed lookup refuses the run.
  Fork heads, authors who fail that check and unlisted events, including
  `pull_request_target`, fail red.
  The canon also refuses `workflow_run` unless its head repository is
  this repository and its triggering actor is an allowlisted bot or has
  `admin`, `maintain` or `write` permission; a failed permission lookup
  refuses the run.
  The repository setting is required because a fork can edit its own
  workflow and remove `authz`: private repositories must set Settings >
  Actions > General > Fork pull request workflows > Run workflows from
  fork pull requests OFF; public repositories must require approval for
  all external contributors. The runner group must be restricted to the
  org's private repositories, with Allow public repositories OFF.
  With that setting, an outside fork run is disabled or held for approval;
  the dispatcher's identity gate decides who gets a runner and `authz`
  supplies the readable red verdict. A cancelled or red `authz` cannot
  satisfy the required check. A maintainer can push a fork's branch to
  this repository and open a same-repository pull request.
  Every other job directly needs `authz`; the required `gate` aggregate uses
  `if: ${{ always() }}` so cancellation cannot skip the required check.
  GitHub counts a required check skipped by its condition as success, so
  `!cancelled()` could allow a cancelled run to merge without tests.
  The aggregate's first step prints a GitHub error annotation and exits
  with status 1 when `needs.authz.result != 'success'`, before any checkout
  or repository code.
  Each later aggregate step requires `needs.authz.result == 'success'`
  before applying the existing docs-only rule: authz and preflight must
  succeed, then differential and pytest must both succeed, or both be
  skipped only for a proven docs-only change.
  Branch protection must make both `authz` and `gate` REQUIRED status
  checks, and the workflows must run on every PR without path or branch
  filters. CI jobs keep their literal pool runner arrays, with authz
  copied byte for byte from the org canon.
  The other jobs retain the
  `["self-hosted","pf-ci","pf-ci-x64"]` runner array, preserving their class
  until `pf-ci-crj` is live; no hosted fallback or Dependabot exclusion is
  allowed. Credential/release jobs stay unchanged, pending `pf-ci-release`.
- The README describes the latest release and becomes the PyPI page of the
  next release: when a version is tagged, update its "Releases and this
  README" section, its engine-pairing table and its release markers.
