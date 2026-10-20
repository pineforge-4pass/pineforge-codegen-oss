# SEC846 — codegen baseline promotion

Policy source read: `/Users/haoliangwen/.config/laneboss/orgs/pineforge/ci-labels.md`,
sections "The `authz` job" and "The required aggregate job" authorized by
the brief. Short Addendum 4 and the canonical `pull_request` author fallback
updated at 16:02 (CORE-TOP #850–#853) govern this pass.

## PR BODY

Add a first `promote-guard` job and require its success before the existing
`promote` credential job can start. The guard runs only inline policy code,
with `permissions: {}`, no checkout and no secrets. It uses the existing
promotion job's `ubuntu-latest` runner.

For same-repository, non-fork authors outside the canonical associations
and bot allowlist, copy the canon's nested author permission fallback,
including its comments and refusal wording, into the existing
`pull_request_target` arm. This lets a private organization member reported
as `CONTRIBUTOR` proceed with a successful `admin`/`maintain`/`write`
author lookup and the existing merger authorization.

Workflow: `.github/workflows/promote-baseline.yml` — NOT RUN.

- Triggers remain `pull_request_target: types: [closed]` at
  `.github/workflows/promote-baseline.yml:74` and `workflow_dispatch` at
  `.github/workflows/promote-baseline.yml:79`, with all dispatch inputs intact.
- The credential job depends on `needs: [promote-guard]` at
  `.github/workflows/promote-baseline.yml:169`; its existing merged-or-dispatch
  `if` at `.github/workflows/promote-baseline.yml:170` has no status function,
  so a failed guard prevents the job from starting.
- App secret references remain `secrets.PINEFORGE_APP_ID` at
  `.github/workflows/promote-baseline.yml:274` and
  `secrets.PINEFORGE_APP_PRIVATE_KEY` at
  `.github/workflows/promote-baseline.yml:275`.
- Workflow-level `id-token: write` remains at
  `.github/workflows/promote-baseline.yml:105`. The GCP authentication action
  remains at `.github/workflows/promote-baseline.yml:297`, using the unchanged
  workload identity provider and service account at lines 299 and 300.
  The guard's explicit empty permissions prevent it from inheriting OIDC.

Every `pull_request_target` close follows this order, including an unmerged
close:

1. Refuse a head repository other than `github.repository`.
2. Refuse unless the head's fork flag is exactly `false`.
3. Accept `OWNER`/`MEMBER`/`COLLABORATOR` authors or the exact bot allowlist
   `dependabot[bot] pineforge-app[bot]`. For every other author, including a
   `CONTRIBUTOR`, require `admin`, `maintain` or `write` from
   `gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission`
   at `.github/workflows/promote-baseline.yml:148`. A failed lookup yields
   `none`; failed, empty and non-write permissions refuse before any
   unmerged exit or merger check.
4. If unmerged, pass without effect; `promote` remains skipped by its
   existing condition.
5. If merged, refuse an empty merger. Otherwise require the merger to be
   an allowlisted bot or have `admin`, `maintain` or `write` from
   `gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission`
   at `.github/workflows/promote-baseline.yml:160`. Failed, empty and
   non-write merger lookups refuse independently of the author's permission.

Manual dispatch passes under GitHub's repository write-access requirement.
Every other event refuses.

Exact refusal message templates (shell substitutions are shown literally):

```text
pull request from another repository (${HEAD_REPO:-unknown})
pull request from a fork
author ${PR_AUTHOR:-unknown} (${ASSOCIATION:-NONE}) has permission '$perm', not write, and is not an allowlisted bot
pull_request_target merging actor is unknown
pull_request_target merging actor ${MERGED_BY:-unknown} has permission '$perm', not write
event $EVENT does not run CI (pull_request_target and other events are not allowed)
```

Every refusal preserves the canonical output and exits 1:

```text
::error title=authz::$1
authz: REFUSED: $1
```

The first line is the error annotation; the second is appended to
`GITHUB_STEP_SUMMARY`. A failed author or merger lookup takes its respective
permission refusal with `perm=none`; an empty or other non-write value uses
that same role's message.

`tests/test_pr_workflows.py` now compares the actual workflow's entire
guard job with an independent literal policy updated to the current canon,
checks its first-job position, the two existing triggers, and the credential
job's dependency and condition.

The authored decision table extracts the real workflow's `run:` script and
will execute it with bash when pytest runs. Only `gh` is stubbed: it validates
the CLI arguments and returns separate permission text and exit status for
the author and merger usernames in their actual collaborator endpoints.
Cases also assert exact annotation/summary text and ordered lookup calls,
so an author lookup cannot stand in for a merger lookup. No inherited
credentials, real `gh` or shell startup files are used by the harness.
Missing bash fails explicitly with `NOT RUN`; it is never silently skipped.
The decision table and mutations remain NOT RUN in this lane.

New and revised expected decisions (same repository, non-fork and merged
unless stated otherwise):

| Author | Author lookup | Merger lookup | Expected exit |
| --- | --- | --- | --- |
| `CONTRIBUTOR` | `write` | `write` | 0 |
| `CONTRIBUTOR` | `admin` or `maintain` | `write` | 0 |
| `CONTRIBUTOR` | `read` | not reached | 1 |
| `CONTRIBUTOR` | failed, including a failed call that prints `write` | not reached | 1 |
| `CONTRIBUTOR` | empty | not reached | 1 |
| `NONE`, not an allowlisted bot | `none` | not reached | 1 |
| `CONTRIBUTOR` | `write` | `read` | 1 |
| `CONTRIBUTOR`, unmerged | `write` | not reached | 0; promotion skipped |

Existing fork, unmerged fork, missing head/fork flag, owner/member/collaborator,
bot author/merger, merger failure/empty/non-write, own unmerged close,
dispatch and unsupported-event decisions remain represented.

Must-fail cases mutate the actual workflow, require each target to occur
exactly once, and expect the literal policy to reject the changed text.
The execution mutations additionally send their mutated script to bash:

- Replace the actual guard script with `exit 0`; make `fail` a no-op.
- Delete the fork check.
- Delete the author permission lookup and exercise the
  `CONTRIBUTOR`-with-`write`, writer-merger success case.
- Make a failed author lookup produce `write` and exercise the failed
  author lookup refusal case.
- Delete the merger lookup and exercise a writer author merged by a reader,
  so reusing the author's `perm=write` is caught.
- Make a failed merger lookup produce `write`; delete the empty-merger check.

Other structural mutations cover:

- Delete each of `EVENT`, `REPO`, `HEAD_REPO`, `HEAD_IS_FORK`,
  `ASSOCIATION`, `PR_AUTHOR`, `MERGED`, `MERGED_BY`, `GH_TOKEN` and `ALLOWED_BOTS`.
- Delete the head repository or fork refusal.
- Expand author associations or the bot allowlist.
- Delete either role's lookup, accept either role's failed lookup as `write`,
  or accept `read`/`none` author or merger permissions.
- Bypass merged-PR checks, allow an extra event, or make the default arm pass.
- Remove the guard's empty permissions, add `id-token: write`, add a
  `secrets.` reference, or add checkout to the guard.
- Remove or override `promote.needs`, add an `always()` bypass, rename the
  first guard, change the credential runner, or change either trigger.
- Append duplicate `needs`, `if`, `runs-on` or `steps` keys after the intact
  credential-job header to exercise the override refusals directly.

These are authored cases, NOT RUN; no test result is claimed. Existing
concurrency and trusted gate pins are unchanged. `CONTRIBUTING.md` describes
the trigger, refusal order, private-member author fallback, independent
merger requirements, failed lookup refusal, unmerged close and manual
dispatch. Every added or rewritten documentation sentence is quoted
verbatim in `COMMIT-MSG-SEC846.md`.

The existing credential-job condition at
`.github/workflows/promote-baseline.yml:170` is exactly:

```yaml
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true }}
```

It requires a merged PR or manual dispatch. It does **not** require base ref
`main`, and the trigger at line 74 has no `branches:` filter. Every use of
the PR base ref is listed here:

```text
.github/workflows/promote-baseline.yml:196:          BASE_REF: ${{ github.event.pull_request.base.ref || 'main' }}
.github/workflows/promote-baseline.yml:200:          git fetch --quiet origin "$BASE_REF" "+refs/pull/$PR_NUMBER/head:refs/remotes/origin/pr-head"
.github/workflows/promote-baseline.yml:201:          if ! git merge-base --is-ancestor "$MERGE_SHA" "origin/$BASE_REF"; then
.github/workflows/promote-baseline.yml:202:            echo "::notice::merge commit $MERGE_SHA is not on origin/$BASE_REF; skipping"
```

No base-ref condition was added: the workflow reads the payload's base
branch, and this addendum requires the remaining guard and promotion flow
to stay intact.

WIF second layer, read only with `git show origin/main:<path>` in
`/Users/haoliangwen/code/pineforge-workflow`: `terraform/gcp/github-wif.tf:129`
through line 134 requires `assertion.repository_owner_id` to match the
configured owner ID, `assertion.repository_id` to belong to the promotion
repo IDs, `assertion.repository` to belong to the promotion repo names,
`assertion.ref == refs/heads/main`, and
`assertion.job_workflow_ref == <repo>/.github/workflows/promote-baseline.yml@refs/heads/main`.
The workflow path and main ref are declared at lines 69 and 81.
`docs/VERIFY.md:1180` through line 1186 describes the same main-copy pin.

A `pull_request_target` run executes main's own workflow with
`GITHUB_REF=refs/heads/main`. Those claims satisfy WIF for any such run in
a listed promotion repository, including a run for a fork PR. WIF does not
distinguish that fork PR from our own PR: `promote-guard` is the layer
that makes that distinction before any credential step.
The older statement about fork `pull_request` token restrictions in
`docs/VERIFY.md:1187` is not a refusal of a base-context
`pull_request_target` run.

Blast radius, from `docs/VERIFY.md:1178` through line 1179:
the `pineforge-promotion` service account has Cloud SQL client on the
registry instance, accessor on the registry-db secret, and read on the
evidence bucket. Terraform and the service account configuration are unchanged.

As required by Addendum 2, `.github/workflows/gate.yml` on the locally
available `origin/main` remains the accepted existing pattern and is
unchanged in this lane. The readback shows:

```text
.github/workflows/gate.yml:7:  pull_request_target:
.github/workflows/gate.yml:8:    types: [opened, synchronize, reopened, edited, ready_for_review]
.github/workflows/gate.yml:27:          ref: ${{ github.sha }}
.github/workflows/gate.yml:28:          path: .docs-gate
.github/workflows/gate.yml:29:          persist-credentials: false
.github/workflows/gate.yml:35:          ref: ${{ github.event.pull_request.head.sha }}
.github/workflows/gate.yml:36:          path: .candidate
.github/workflows/gate.yml:37:          persist-credentials: false
.github/workflows/gate.yml:52:          checker=.docs-gate/.github/pineforge/ci/codegen-docs-check.mjs
.github/workflows/gate.yml:54:            node "$checker" "$PWD/.candidate" "$BASE_SHA" "$HEAD_SHA" "$BASE_BRANCH" > "$RUNNER_TEMP/codegen-docs-diff.json"
.github/workflows/gate.yml:136:    permissions:
.github/workflows/gate.yml:137:      statuses: write
```

The preflight checks out the PR head as data in `.candidate` without
persisting credentials and runs the checker from the trusted `.docs-gate`
copy. The file has no repository-secret references; the `gate` job has
only `statuses: write`. The separate gate revert/template work is not
part of this codegen promotion lane.

Repository settings readback and a real CI run: NOT RUN (no network).
No PR was opened; the supervisor owns commit, push and CI validation.

## NOT CHANGED

- Existing promotion trigger types, dispatch inputs, job condition, runner,
  secret handling, App token scope, OIDC permissions/authentication, checkout
  steps, tree/gate checks, database proxy and baseline promotion steps:
  only the requested guard and dependency were added to the workflow.
- No status-function job exists in this promotion workflow, so it needs no
  aggregate refuse-first step; the existing plain job condition honors the
  guard dependency.
- `.github/workflows/gate.yml` remains unchanged. In this worktree it still
  declares the accepted `pull_request_target` pattern described above;
  Addendum 2 explicitly requires no gate edit in this branch.
- `campaign/ci/promote-baseline.yml`, `campaign/ci/codegen-gate.yml`, engine
  workflows and license workflows are outside this worktree. No template
  synchronization or changes in those repositories are claimed.
- Runtime, codegen, dependency files, versions, memory and instruction files
  are unchanged. No file was deleted; nothing was staged or committed.

## DECISION

- Use the requested `promote-guard` variant because the canonical `authz` intentionally refuses all `pull_request_target` events.
- Retain the credential workflow's hosted `ubuntu-latest` runner for both jobs until the separate pf-ci-release migration.
- Keep the order head repository, fork flag, author, unmerged pass without effect, merger check; an unmerged fork or unauthorized author refuses before promotion is skipped.
- Copy the current canon's nested author case, private-membership comments, permission lookup and refusal text unchanged; a `CONTRIBUTOR` author with `admin`/`maintain`/`write` may proceed to the independent merger check.
- Stub permission replies per username and assert the ordered author/merger endpoints; a writer author merged by a reader must refuse.
- Add execution mutations for deleting the author lookup and accepting a failed author lookup as `write`, as well as deleting the independent merger lookup.
- Keep canonical fork/author/default messages and `authz` output; retain the prior pass's `pull_request_target merging actor` wording for the merger refusal.
- Keep `permissions: {}` and `GH_TOKEN: github.token` as the canon specifies; an unavailable metadata permission lookup fails closed.
- Keep the existing base-ref behavior; the workflow has no base-main requirement, and this pass does not add one.
- Describe WIF as pinning the base repository's main workflow, not as rejecting fork-headed `pull_request_target` events; the guard must reject those before credential use.
- Leave engine and workflow-repository template edits to their respective lanes because they are outside this worktree; supervisor must confirm textual template/codegen alignment across the resulting diffs.
- Keep the accepted main `gate.yml` unchanged as Addendum 2 directs.
- The explicit lane no-execution instruction governs this edit-only handoff; the engine-enabled verification required by AGENTS.md remains NOT RUN.

## NOT RUN

| Changed file | Validation |
| --- | --- |
| `.github/workflows/promote-baseline.yml` | NOT RUN |
| `tests/test_pr_workflows.py` | NOT RUN |
| `CONTRIBUTING.md` | NOT RUN |
| `COMMIT-MSG-SEC846.md` | NOT RUN |
| `REPORT-SEC846.md` | NOT RUN |

Python, Node, npm, pytest, test collection, workflow parsing, actionlint,
the full engine-enabled suite, the corpus regression sweep,
`ci-policy-check.py`, the TypeSafe diff guard, live permission lookups and
GitHub Actions runs: NOT RUN. Only allowed read-only source/diff inspection
was used; all writes used `apply_patch`.
