# Contributing to pineforge-codegen

This is the source-available PineScript v6 to C++ translator. Read the
[license and contribution terms](LEGAL.md) before sending a material change.
Contributions use the Developer Certificate of Origin: add a `Signed-off-by`
line with `git commit -s`. Material contributions also require a Contributor
License Agreement so PINEFORGE LLC can include them in its commercial licenses;
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

`promote-baseline.yml` runs from the base repository on `pull_request_target`
closes and `workflow_dispatch`, and its credential job depends on
`promote-guard`. The guard has empty job permissions, holds no secrets, and
checks out no code. Every pull request close first requires a head in this
repository with `fork == false`, even when unmerged. An author outside
`OWNER`/`MEMBER`/`COLLABORATOR` and the
`dependabot[bot]`/`pineforge-app[bot]` allowlist must have
`admin`/`maintain`/`write` permission returned by GitHub, including a
`CONTRIBUTOR` whose organization membership is private. A failed author
lookup, an empty permission, or a non-write permission refuses the close
before any unmerged exit or merger check.
A merged pull request also requires an allowlisted merger or a successful
`admin`/`maintain`/`write` permission lookup for the merger. An empty merger,
a failed merger lookup, or an empty or non-write merger permission refuses
promotion.
An unmerged close of our own allowed pull request passes the guard with
promotion skipped. Manual dispatch requires repository write access, and
every other event is refused.

App-token minting jobs use two main-only GitHub environments: human-dispatched
release jobs use `release`, with the owner as required reviewer; automatic
baseline promotion (including its manual re-fire) uses `release-bot`, without
a reviewer. The `workflow-lint` preflight checks every workflow and requires a
literal, non-empty job environment for App-token actions or private-key secret
use, and flags recognizable scripted minting for human review. Keep this change
in draft until the owner confirms both environments and their same-name secret
copies exist. For rollback, remove the added `environment:` lines and revert
their policy pins together; repository-level secret copies remain available
until the owner deletes them after the dry runs.

## npm publication (operators)

The release and npm publication workflows run on GitHub-hosted runners.
Run both workflows with `workflow_dispatch` on `main`.
`release.yml` publishes to PyPI, commits and pushes the release tag, then
creates the GitHub Release and notifies the release hub. After the tag exists
on main and the release job is green, the operator doing the cut, using a
GitHub account with write access, publishes npm with this separate dispatch:

```sh
gh workflow run publish-pyodide.yml --repo pineforge-4pass/pineforge-codegen-oss --ref main -f tag=<tag> -f dry_run=false
```

Use the existing release tag in place of `<tag>`. Set `dry_run=true` to
validate first. A tag push alone publishes nothing. A green `release.yml`
run does not mean npm published; watch the `publish-pyodide.yml` run.
Every npm run requires an existing release tag, including dry runs and
placeholder claims. Its first job, `tag-guard`, has only Contents read
permission and checks out main with credentials persistence disabled. It
checks the dispatch ref is `refs/heads/main`, validates the release pattern
(`vX.Y.Z` or `vX.Y.Z-{alpha,beta,rc}.N`) before using the input in git,
fetches tags and full main history, peels the tag to a 40-hex commit and
checks that commit is an ancestor of `refs/remotes/origin/main`.
A missing tag, a tag that cannot peel to a commit, an invalid name or a tag
outside main fails the run before either npm job can start. Publish checks
out the verified commit SHA with credentials persistence disabled, verifies
its `VERSION`, and runs the gate before publishing. The workflow run stays
at `refs/heads/main` while its checkout uses the pinned commit; moving the tag
after verification does not change what gets built.

For a manual npm dry run, dispatch `publish-pyodide.yml` on `main` with
`dry_run=true` and an existing release `tag` on main. For a real npm retry
after a successful tag push, dispatch on `main` with the existing release
`tag` and `dry_run=false`. A placeholder claim uses `claim_placeholder=true`
and the same required tag guard. The guard fails a dispatch at another ref;
both npm jobs also retain their main-ref job conditions and require the
guard to succeed.
The guard and job conditions live in the workflow file on `main`, but GitHub
executes the file from the dispatched ref, so they alone cannot stop a user
with write access who pushes and dispatches a modified workflow on another
branch. The server-side bound is the GitHub environment `npm-publish`: both npm
jobs (`publish` and `claim-npm`) run in it, and it carries a `main`-only
deployment-branch policy, so a run from any other ref is refused by GitHub. The
npm trusted publisher for the package is set by the repository owner to require
that environment (owner decision NPM-TP; that setting lives on npmjs.com and
cannot be read from this repository), so a publish from a workflow that does not
declare the environment fails at npm.

## Change and review notes

- Add or update tests for behavior changes. Keep the engine corpus compiling;
  an intentional support drop needs the rationale required by [AGENTS.md](AGENTS.md).
- If existing scripts emit different C++, list the affected public-corpus
  translation units and whether their trades move when run with the matching
  engine.
- Keep [CHANGELOG.md](CHANGELOG.md) current for user-visible changes. Its
  release-note policy applies to both stable and prerelease tags.
- Pull request CI requires a non-draft, same-repository PR to `main`, with
  repository write permission for its author, actor and rerunning actor (or a
  fixed allowed bot). Fork PRs, including documentation-only PRs, do not enter
  GKE. A maintainer can push the branch to this repository and open a PR from
  it. Every admitted diff runs the full test matrix and differential gate.
  See [CI admission and check mapping](docs/ci.md) for the hosted authorization
  and result jobs, pod permissions, and operator setup.
- The README describes the latest release and becomes the PyPI page of the
  next release: when a version is tagged, update its "Releases and this
  README" section, its engine-pairing table and its release markers.
