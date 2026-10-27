# Pull request CI

`gke-pr.yml` runs pull request tests on GKE runner pods after a hosted admission
job accepts the exact head. It uses `pull_request_target` against `main`, so
GitHub executes the workflow definition from the base repository. Candidate
workflow files are fetched as data and checked by the base tree's
`tests/test_gke_pr_admission.py`; the hosted jobs never check out candidate code.

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
`package.json` cache metadata are fetched. Directory entries are skipped;
regular blobs are limited to 100 files, 1 MiB each and 4 MiB total, with decoded
lengths checked against the tree.
The scanner bounds lines at 4,096 characters, files at 20,000 lines and
structure at 40 levels. It refuses ambiguous YAML structure, duplicate keys,
anchors, aliases, tags, merge keys, dynamic runner expressions, grouped runner
mappings, unsupported flow values and control characters. Ordinary quoted text,
GitHub expressions in environment values, and script block contents are kept
separate from routing structure.

Every admitted PR, including a documentation or workflow change, runs the full
matrix and differential checks. There is no cheap-skip classifier to trust.
The hosted result requires each prerequisite to succeed, rechecks live PR and
actor identity, and posts the `gate` status to the immutable event head. A
failure, skip, cancellation, missing output, stale head or refusal fails the
result. Status publication failure also fails the job.

| Previous PR context | Current context |
| --- | --- |
| Test / authz | Test / authz, unchanged and hosted |
| Test / changes | GKE PR / admit |
| Test / workflow-lint | Test / workflow-lint runs on push and dispatch; GKE PR / workflow-lint checks the candidate |
| Test / package-files | GKE PR / package-files |
| Test / pytest (3.11, 3.12, 3.13, 3.14) | GKE PR / pytest, with the same versions |
| Test / test-check | GKE PR / test-check, hosted |
| Pyodide Gate / preflight-docs | GKE PR / admit |
| Pyodide Gate / differential | GKE PR / differential |
| Pyodide Gate / pytest (3.11, 3.12, 3.13, 3.14) | The shared GKE PR / pytest matrix |
| Pyodide Gate / gate and the `gate` commit status | GKE PR / gate and the same `gate` commit status, hosted |

The shared pytest matrix runs the same command as both former PR matrices.
The differential leg retains Python 3.14, Node 22, the Pyodide selftest,
dependency audit and full gate. Package metadata and entity checks run on every
admitted diff, as does the workflow/App-environment guard.

All pod jobs use the bare scalar `gke-8cpu-16gb-amd64`. The image supplies the
engine toolchain, Python 3.12 with venv, git, git-lfs, jq, curl, actionlint,
shellcheck and gh. Pinned setup actions supply the other Python versions and
Node/npm 22. The pod jobs have only `contents: read`, persist no checkout
credentials, receive no secret, OIDC permission or environment, and use no
Actions cache. Hosted admission has read-only contents permission; only the
hosted result can write statuses.

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

`test.yml` keeps its push-to-main and dispatch jobs hosted and has no
pull-request trigger. Pull-request checks run through `gke-pr.yml`, which admits
and executes the candidate head.
The existing App-environment guard keeps its authorization dependency and
blocking step. Hosted workflow lint uses the checked-in actionlint configuration
and the exact checkout head.
`gate.yml` retains only its hosted schedule and dispatch
legs. Those trusted routes require a separate runner admission entry before
they can move. PyPI/npm publication and baseline promotion remain hosted.

The only `pull_request_target` workflow is `gke-pr.yml`.
`promote-baseline.yml` runs on pushes to `main` and manual dispatch from `main`.
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
repository change. Enable only
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

The checker and its mutation controls use only Python's standard library plus
pytest for the tests. File-reading modes list their inputs, refuse credential
paths and `*.env`, and reject symlinks before opening files. Full tests,
engine-enabled corpus compilation and the Pyodide gate must run in CI or on
the designated build box; a source inspection is not an execution verdict.
