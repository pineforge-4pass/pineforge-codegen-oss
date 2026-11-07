# Engine main touched integration

`.github/workflows/engine-main-touched.yml` adds an ordinary
`engine-main-touched` job check. Every PR to `main` gets a visible changes
decision, without an event path filter. A documentation-only diff records its
base/head and reason, visibly skips the native job, and completes the result
job as an explicit native skip.

PR admission reuses `tests/test_gke_pr_admission.py --identity` from the PR's
base commit: an open, non-draft, same-repository, non-fork PR and authorized
author, actor and rerunner are required. Manual proof dispatch checks actor and
rerunner write permission and requires two immutable, full 40-hex public
codegen commit refs. Neither route uses secrets, OIDC, cached builds or
persisted checkout credentials. All jobs use standard `ubuntu-24.04`.

## Selection contract

The definition checkout supplies `scripts/ci_engine_main_touched.py`; the
separate candidate checkout supplies product code and existing tests. The
driver verifies both checkout HEADs. Normal PRs test `pull_request.head.sha`,
never the synthetic merge checkout. The definition SHA and tree are recorded
separately from the tested codegen SHA and tree.

The driver reads `git diff --name-status -z --find-renames BASE...HEAD`.
Deleted paths and both sides of renames participate. Diff/read/parse errors
fail the decision. Native triggers are:

- `pineforge_codegen/**`, including all `codegen/emit_*`, checked settings,
  constructors and future factory modules;
- C/C++ headers, sources and template suffixes anywhere, including the existing
  `tests/golden/matrix_eigen_pca.cpp`;
- `tests/_compile.py`, `tests/_e2e.py`, `scripts/gen_host_members.py`, the new
  workflow/driver/unit test, and changed tests calling a native harness;
- removed test files, conservatively, since the head cannot classify their
  former native dependencies.

Selected targets are explicit function or class-method selectors, rather than
the whole suite: changed test files, tests importing a changed module, test
filenames matching its underscore-delimited topic, the golden tests for changed
golden C++, and lifecycle tests for constructor emitter changes. Static AST
discovery follows local helpers and fixtures to identify native and input
requirements. This is a reviewable mapping, not a complete impact graph.
Unmatched emitter modules still get the mandatory smoke.

Every native run includes these existing, unchanged seams:

- checked construction's original error, both `legacy=False` and `legacy=True`;
- minimal strategy compilation;
- checked-settings metadata/exception wrapper emission;
- checked-settings values and receipt at runtime, with its existing optimization
  parameters.

Known non-public fixture/feed/population requirements are excluded by target
and listed with their input names. Historical `reference_codegen` comparisons
are listed as outside this touched integration scope. Public corpus/replay
inputs are initialized only when selected cases require them. An unrecognized
input requirement that causes a selected test to skip fails the run; it cannot
be counted as native coverage.

## Native bootstrap and evidence

The native job checks out the public `pineforge-engine` **main branch at
runtime**. It logs that checkout's commit/tree and resolution time and verifies
HEAD equals its freshly fetched `origin/main`. No engine SHA or release tag is
an authored pin.

The fresh build follows CONTRIBUTING's source-layer recipe:

```sh
cmake -S engine -B engine/build -DCMAKE_BUILD_TYPE=Release \
  -DPINEFORGE_BUILD_TESTS=OFF -DPINEFORGE_BUILD_SOURCE_LAYER=ON \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++
cmake --build engine/build --parallel 8
```

The same numeric worker parameter drives build and pytest, restricted to 1–8;
dispatch offers 1, 2, 4 and 8, and PRs use 8. No authored job time limit exists.
Preflight requires public engine and checked-settings headers, the real
`build/include/pineforge/version.h`, a nonempty static runtime archive, Eigen
and a native compiler. A compiler check also requires the settings API macro,
so the existing regression's guarded assertions cannot silently disappear.

Before importing any tested product/harness, the driver sets
`PINEFORGE_ENGINE_INCLUDE`, `PINEFORGE_GENERATED_INCLUDE`,
`PINEFORGE_ENGINE_LIB`, `PINEFORGE_EIGEN_INCLUDE` and `CXX`. It verifies the
imported codegen comes from the candidate directory. A selected corpus case
also gets `PINEFORGE_ENGINE_CORPUS`, public strategies and a smudged public
1m feed; missing required inputs fail.

pytest runs once for the selected targets with numeric xdist workers. The
controller records collected nodeids, call outcomes, phases, skip/xfail
details and its actual exit. Zero collection, a missing selected target, a
missing construction parameter, any selected case without an ordinary call,
or any skip/xfail fails even if pytest returns zero. A nonzero build/compiler/
pytest exit remains nonzero; assertion failures retain pytest's exit.

Always-uploaded decision/native artifacts contain selection reasons, excluded
targets, selected selectors, build/preflight/pytest logs, native identity,
actual collected nodeids and JUnit when pytest reaches execution. Setup
failures retain the evidence produced up to that point; absence of JUnit is
not test evidence. There are no cleanup or removal steps.

## Hosted proof requests for ROOT

Publish and register this workflow through the authorized CI publication path
first. GitHub requires a dispatchable workflow to exist on the default branch;
an unregistered branch-only file cannot provide manual proof. Once registered,
use the same published definition ref for all three runs. These are operator
requests, not commands executed by the source author:

```sh
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f codegen_base_ref=099a12b622ec8e9179695c60dd191b5cb6228dc3 \
  -f codegen_head_ref=106817c2d3838c94f19f0e1a851dfd217711c806 -f workers=8
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f codegen_base_ref=FIX_BASE_SHA -f codegen_head_ref=FIXED_HEAD_SHA -f workers=8
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f codegen_base_ref=DOCS_BASE_SHA -f codegen_head_ref=DOCS_HEAD_SHA -f workers=8
```

All placeholders are actual published refs/commit IDs, not expected-result
inputs. RED must show the two existing checked-construction assertion failures
after successful build/preflight, not setup or collection failure. GREEN must
show ordinary execution of those same cases from the separately fixed head.
SKIP must use a real documentation-only Git diff and visibly skip the native
job. Compare the independently recorded definition/codegen/engine identities;
engine main can advance between runs. Source inspection, unit tests and
metadata alone establish none of these hosted outcomes.

## Integration owned by ROOT / CI coordination

This additive workflow writes no `gate` status and changes no existing
aggregate. The current required `gate` does not depend on it. ROOT must arrange
required-check/merge-consumer integration for `engine-main-touched`, binding
the current PR head, with CI-CX owning any changes to existing workflow/checker
files. The existing parity campaign and publication gates remain binding.

ROOT also owns reconciling AGENTS/CLAUDE and docs/ci.md's statements that native
tests are absent from CI, and the newer CORE policy of PR touched+smoke with
full runs on main. This workflow adds no main-push full run. Existing policy
tests and pinned workflow lint must be run by ROOT before publication; no
policy relaxation is assumed. The new selector tests are unit coverage only;
they do not replace the hosted RED/GREEN/SKIP evidence or the engine's tests.
