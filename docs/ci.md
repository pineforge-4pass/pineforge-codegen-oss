# Pull request CI

**Bootstrap checkpoint:** this commit registers the shared proof workflow and stages its trusted admission rules. The required PR-native prerequisite and automatic full-native main-push trigger are inactive here. Descriptions of those behaviors below describe the following activation target; they are not a claim that this bootstrap has installed or executed them. Activation still requires the real hosted proofs, input provisioning and review.

This document describes the source candidate after staged adoption; it does
not record installation or successful hosted validation.

`gke-pr.yml` runs pull request tests on standard `ubuntu-24.04` after trusted
admission accepts the exact head. It uses `pull_request_target` against `main`, so
GitHub executes the workflow definition from the base repository. Candidate
workflow files are fetched as data and checked by the base tree's
`tests/test_gke_pr_admission.py`. Admission and result check out only the trusted
base. Payload jobs execute the admitted head; native changes selection reads
candidate Git objects as data with the trusted base driver.

Admission requires an open, non-draft PR from this repository, with a non-fork
head and `main` as its base. The author, original actor and rerunning actor each
need `admin`, `maintain` or `write` repository permission. The fixed
`dependabot[bot]` and `pineforge-app[bot]` exceptions match the existing authz
policy. A missing identity, failed API lookup, unexpected permission or stale
event head refuses admission. The original `test.yml` authz job remains hosted
on push and dispatch and retains its author permission fallback.

Before setting `allowed=true`, admission reads the immutable head's recursive
Git tree, refuses truncation, symlinks, submodules, unsafe names and oversized
inputs, then runs pinned actionlint with the base configuration and the base
security rules. Only workflow/action files, the actionlint configuration and
`package.json` cache metadata and native driver source are fetched. The entire
native callee and driver must equal the installed base source. Directory entries are skipped;
regular blobs are limited to 100 files, 1 MiB each and 4 MiB total, with decoded
lengths checked against the tree.
The scanner bounds lines at 4,096 characters, files at 20,000 lines and
structure at 40 levels. It refuses ambiguous YAML structure, duplicate keys,
anchors, aliases, tags, merge keys, dynamic runner expressions, grouped runner
mappings, unsupported flow values and control characters. Ordinary quoted text,
GitHub expressions in environment values, and script block contents are kept
separate from routing structure.

Every admitted PR, including docs changes, retains the full existing matrix and
differential checks. The added `engine-main` prerequisite calls the exact
base-owned `engine-main-touched.yml` route. Its trusted `changes` job reads the
admitted base/head diff and inventories; only a successful, identity-bound
non-native decision permits the native job to skip. Emitter/generated-C++ and
native harness/test changes build public ENGINE MAIN fresh and execute touched
tests plus unchanged smoke. See [engine-main-ci.md](engine-main-ci.md).
The same native workflow runs the **full repository suite once on every main
push**, including docs changes, without path filters or cancellation groups.
Main targets do not come from the PR diff selector. It checks out the immutable
event `after` SHA, binds `before` independently and never substitutes a later
main tip. The real engine MAIN build, compiler, Eigen, generated header,
archive, public corpus/LFS feed, clang and codegen history are provisioned
before collection/imports. Normal native job check runs attach to the pushed SHA.

The hosted result requires all seven prerequisites to succeed, consumes actual
changes/native results and the bound plan digest, rechecks live PR and
actor identity, and posts the `gate` status to the immutable event head. A
failure, skip, cancellation, missing output, stale head or refusal fails the
result. The sole intended skip is the reusable native job after a genuine
non-native decision; the reusable prerequisite itself must succeed. Status
publication failure also fails the job.

| Previous PR context | Current context |
| --- | --- |
| Test / authz | Test / authz stays on `ubuntu-24.04` for push and dispatch |
| Test / changes | GKE PR / admit; Test / changes stays on `ubuntu-24.04` for push and dispatch |
| Test / workflow-lint | Test / workflow-lint stays hosted on push and dispatch; GKE PR / workflow-lint checks the candidate |
| Test / package-files | GKE PR / package-files; Test / package-files stays hosted on push and dispatch |
| Test / pytest (3.11, 3.12, 3.13, 3.14) | GKE PR / pytest, with the same versions; Test / pytest stays hosted on push and dispatch |
| Test / test-check | GKE PR / test-check stays hosted; Test / test-check stays on `ubuntu-24.04` for push and dispatch |
| Pyodide Gate / preflight-docs | GKE PR / admit |
| Pyodide Gate / differential | GKE PR / differential |
| Pyodide Gate / pytest (3.11, 3.12, 3.13, 3.14) | The shared GKE PR / pytest matrix |
| Pyodide Gate / gate and the `gate` commit status | GKE PR / gate and the same `gate` commit status, hosted |
| Native tests absent from the matrix | GKE PR / engine-main calls shared changes/native jobs; consumed by the same `gate` writer |

The shared pytest matrix runs the same command as both former PR matrices.
The differential leg retains Python 3.14, Node 22, the Pyodide selftest,
dependency audit and full gate. Package metadata and entity checks run on every
admitted diff, as does the workflow/App-environment guard.
The compile gate runs in `gke-pr.yml` for pull requests, on the admitted GKE
pods. Pushes to `main` run the retained compile gate in `test.yml` on
`ubuntu-24.04`.

All current PR jobs use the bare scalar `ubuntu-24.04`. Pinned setup actions
supply Python versions and Node/npm 22. Payload jobs have only `contents: read`, persist no checkout
credentials, receive no secret, OIDC permission or environment, and use no
Actions cache. Admission has read-only contents permission; only the result
can write `gate` statuses. The reusable native execution has the same restrictions.
Its proof dispatch checks actor/rerunner write access and actual public
base/head commits and executes the same native implementation. Only its isolated
trusted historical publisher has `checks: write` and `actions: read`; the
callee's candidate execution jobs explicitly keep only `contents: read`.
The caller declares that permission ceiling for the dispatch-only publisher.
It creates a distinct native source check from the real completed job on the
tested SHA and verifies the created record. No second `gate` writer is added.

An Actions runtime/cache token is still available to PR code on a runner.
Consequently the policy also refuses Actions cache restore/save steps and
setup cache inputs throughout the workflow/action data it checks: trusted jobs
must never restore cache entries a PR could write. Differential artifacts are
diagnostics for that run; the result does not download or trust them.
Node setup explicitly sets `package-manager-cache: false` in admission and
the retained gate workflow. The unchanged promotion Node setup is admitted
only while `package.json` has no `packageManager` or
`devEngines.packageManager` opt-in. Adding such metadata requires disabling
that remaining setup default in a separate base change first.

`test.yml` has only push-to-main and dispatch triggers; its current jobs stay
on `ubuntu-24.04`. Pull-request checks use `gke-pr.yml` admission.
The existing App-environment guard keeps its authorization dependency and
blocking step. Workflow lint uses the checked-in actionlint configuration
and the exact checkout head.
`gate.yml` retains only its hosted schedule and dispatch
legs. Those trusted routes require a separate runner admission entry before
they can move. PyPI/npm publication and baseline promotion remain hosted.
The old pytest matrix still lacks native inputs; PR native work is touched+smoke.
The shared main-push native route automates full engine-enabled verification,
including corpus compilation, emitted runtime and history cases, once on each
exact pushed tree. It runs the existing suite without a second smoke pass.
It retains actual per-worker discovery/final inventories, module collection
reports, deselections and all setup/call/teardown outcomes. Missing calls,
skip/xfail, setup failures and collection omissions prevent full success.
Known private/platform limitations remain named unexecuted NEEDS, never a full
GREEN from a narrower public subset. No constant test count establishes proof.
Full mode, inventory digest and successful full completion are bound to the
same plan/source and are required by the historical source-check publisher.
The actual dependency-outcome bridge remains in place for pinned actionlint.
Old push/dispatch jobs retain their existing scope; no later manual full-main
run replaces this job's exact-SHA proof.

The only `pull_request_target` workflow is `gke-pr.yml`.
`promote-baseline.yml` runs on pushes to `main` and manual dispatch from `main`.
Manual dispatch validates `merge_commit` and `head_sha` as lowercase 40-hex, `pr_number` as a positive decimal, `pr_url` for this repository and number, and `merged_at` as `YYYY-MM-DDTHH:MM:SSZ`.
It checks the numbered PR and paginated commit associations with `gh`, requiring one merged PR on `main` whose merge, head, and merge time match the inputs.
API errors, empty responses, mismatches, and multiple associations refuse dispatch.
If dispatch refuses, re-read the values from the merged pull request page or
`gh pr view <n> --json mergeCommit,headRefOid,mergedAt,url`, then dispatch again from `main`.
Never edit the workflow to get past a refusal.
Promotion paginates the pushed commit's associated PRs and requires exactly one
closed, merged PR with that merge commit and base `main`; zero or multiple
matches skip promotion green. It rereads that PR's full record, binds its gated
head, merge commit, author, merger, number, URL and merge time, then checks
authorization and tree equality. Codegen still requires the `gate` context
to succeed on that head (the trusted reporter's status, else a success
check-run) and no failing newest check-run per app and name. The hosted runner,
release environment and WIF workflow-path/main-ref binding remain in place;
the workflow checks out and runs no PR code.

Runner-group and merge-authority configuration are operator work outside this
repository change. Any future PR pod routing must enable only
`pineforge-4pass/pineforge-codegen-oss/.github/workflows/gke-pr.yml@refs/heads/main`
for PR pods; do not enable a caller-controlled reusable workflow or a
`pull_request` workflow. Update consumers of the context mapping above before
relying on the new checks. Consumers should bind the workflow run's exact
path/ref, `pull_request_target` event and
`gke-pr #<PR number> <40-hex head SHA>` title to the current live PR head, then
require the newest matching run to conclude `success`. A missing, skipped,
cancelled or refused run is not success. A push after a live-state read can
leave only an old-head result; the consumer must recheck the current head.

Main's rules judge candidate workflows. A new runner label or a rule relaxation
therefore needs two changes: first expand the base checker/configuration
without using the relaxation in a workflow, then change the workflow.
Tightening a workflow must satisfy the base rules too. Bootstrap this workflow
on `main` and configure the runner entry before expecting it to run for later
PRs; its first adoption PR still needs independent CI evidence.

Native integration likewise needs main-owned policy adoption before activating
the caller. First install the shared implementation and a separately reviewed
temporary policy admitting only the complete old and new canonical graphs,
while the old caller and an inactive main-push trigger remain installed.
The temporary rule permits only the exact main-only push header together with
the new caller; it cannot permit body changes, filters or cancellation.
Register dispatch and its isolated checks permission. Run original touched PR
RED, merged-main full RED with a check-run API record on the tested source SHA,
restored-main full GREEN and docs PR SKIP with one immutable hosted definition,
then activate both routes and the strict checker together. Final policy admits
only the new graph and pins the full callee/driver to base; it refuses removal
of the native prerequisite.
Remove the temporary old-inventory reporter at activation. The exact source
route and proof inputs are in `docs/engine-main-ci.md`.
Focused mode/inventory/missing-input controls must run on a runner with
configured native inputs. Activation also requires the same immutable hosted
definition's actual RED/full GREEN/docs SKIP and exact-source check-run API
readback. Independent review must bind the exact source proposed for
publication. Source preparation establishes neither hosted outcomes nor
activation.

The checker and its mutation controls use only Python's standard library plus
pytest for the tests. File-reading modes list their inputs, refuse credential
paths and `*.env`, and reject symlinks before opening files. Full tests,
engine-enabled corpus compilation and the Pyodide gate must run in CI or on
a runner with configured native inputs; a source inspection is not an
execution verdict.
