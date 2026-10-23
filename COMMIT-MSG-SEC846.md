ci: unify baseline promotion authorization and its literal pin (SEC846)

Require promote-guard before the promotion job can receive App secrets or
an OIDC credential. Copy the engine lane's complete final guard, including
the job header, comments, step name and blank separator. There is no
per-repository difference within that block. The author case remains
verbatim canon, including the private-member permission fallback.

Refuse foreign or fork heads before author authorization, even for an
unmerged close. Require the canonical association/bot/permission rule for
the author, then allow an authorized own unmerged close without promotion.
A merged PR independently needs a nonempty allowlisted merger or a
successful admin/maintain/write lookup for that merger. Failed, empty or
non-write lookups refuse. Manual dispatch remains allowed; other events
refuse. Preserve promote's existing condition, permissions and steps.

Pin one independent literal of the complete block in
tests/test_pr_workflows.py:137 and compare without stripping bytes at
line 216. Real-workflow mutations change the header, step text, bot comment
and blank separator. Retain all previous identity, dependency, secret,
checkout, OIDC and duplicate-key controls. Update the extracted-script
decision table's literal messages and unmerged summary, and retarget the
merger mutations to the copied script. Only gh is stubbed when CI runs the
table; author and merger replies are keyed by username.

Read-only full-block MD5 in engine, codegen and template:
933cc1f869787b37f3b339978d3828a2. The codegen literal has that same hash.
The canon, workflow and literal author cases all have MD5
f684468222dd2d9915868e54fee8accc. These are source measurements, not test
results. All tests and repository scripts are NOT RUN.

The pending merge retains main's documentation, features and tests.
CONTRIBUTING.md already held a marker-free union when this lane began:
the SEC846 paragraph at line 112 precedes main's npm operator section at
line 131. No main text was removed. The supervisor must stage that
unmerged index entry and finish the merge; this lane changes no index,
branch or history.

CONTRIBUTING.md:112, added SEC846 documentation quoted verbatim:

> `promote-baseline.yml` runs from the base repository on `pull_request_target`
> closes and `workflow_dispatch`, and its credential job depends on
> `promote-guard`. The guard has empty job permissions, holds no secrets, and
> checks out no code. Every pull request close first requires a head in this
> repository with `fork == false`, even when unmerged. An author outside
> `OWNER`/`MEMBER`/`COLLABORATOR` and the
> `dependabot[bot]`/`pineforge-app[bot]` allowlist must have
> `admin`/`maintain`/`write` permission returned by GitHub, including a
> `CONTRIBUTOR` whose organization membership is private. A failed author
> lookup, an empty permission, or a non-write permission refuses the close
> before any unmerged exit or merger check.
> A merged pull request also requires an allowlisted merger or a successful
> `admin`/`maintain`/`write` permission lookup for the merger. An empty merger,
> a failed merger lookup, or an empty or non-write merger permission refuses
> promotion.
> An unmerged close of our own allowed pull request passes the guard with
> promotion skipped. Manual dispatch requires repository write access, and
> every other event is refused.

Codegen's SEC846 documentation is additive: there are no removed codegen
doc sentences to quote. The following cross-repository removed passages
were omitted from the original handoffs (READ-CODEX12 E2/T3). Historical
revisions are explicit so their line numbers are reproducible; these
quotations do not claim that codegen edited the other repositories.

Engine 0212cabe2d87e64b9656791ade118be0bdee01bc:docs/ci.md:768,
removed promotion description (including lines 771, 773 and 775):

> (`promote-baseline.yml`, installed from the maintainers' private campaign
> tooling) runs `main`'s own copy on the closed PR
> (`pull_request_target`) and runs no PR code. It requires both statuses to be
> successful on the verified PR head and a merge commit on `main` that carries
> exactly that head's tree (PRs are squash-merged, so the head itself never lands
> on `main`). It hands the campaign tool the merge commit, the PR head and that
> tree, and the tool promotes the pair the merge gate's parity verdict measured
> for the tree; every other case exits green without promoting.

Engine same revision, docs/ci.md:1059, removed runner claim:

> Promotion stays hosted for both target and dispatch triggers.

The engine's merged main now isolates the credential job on pf-gcp-main;
the shared guard remains hosted. These runner differences are outside the
shared guard. No runner change is made by this codegen lane.

Workflow 6d8a82791e87992b64ede831547f12fef66df76f:docs/VERIFY.md:1187,
removed WIF/fork explanation:

>   Fork PRs never could: a workflow run from a fork is not granted write
>   permissions — `id-token: write` included — unless an admin turns on "Send
>   write tokens to workflows from pull requests"; keep it off. The template
>   checks out and runs no PR code under `pull_request_target`.

The replacement explains that base-context fork-headed target events
satisfy the main-pinned WIF identity too: the guard distinguishes their
heads before credential use. The promotion service account can use Cloud
SQL, access the registry-db secret and read the evidence bucket.

Workflow same revision, docs/CODEGEN-DOCS-GATE.md:140, removed installation
and engine-scope claims (including line 144):

> Install all five in the same codegen PR. `promote-baseline.yml` replaces the
> live copy, which is byte-identical to the previous template: it now reads the
> newest `gate` commit status on the PR head (the reporter's in practice; the
> workflow does not check its creator) and still accepts the legacy check-run.
> The engine repo's own copy is an older revision; this
> change touches only the codegen branch, so the engine needs no update for it.

Same file:153, removed target-trigger trust description:

> The checker is builtins-only and uses the same rule. PR CI uses the trusted
> default-branch workflow definition (`pull_request_target`), not the PR's
> editable workflow. Its classifier reads the exact head as data in a separate
> checkout, executing only the trusted checker. Its preflight resolves Git commits/tree/diff; its docs check proves

Same file:167, removed Actions event policy:

> policy when installing: it must permit this trusted workflow's
> `pull_request_target` event. Do not weaken permissions or inject secrets into
> the jobs that execute PR code.

Same file:171, removed PR pytest description:

> PR pytest runs in the trusted gate workflow. The separate test workflow

Same file:175, removed universal timeout claim:

> The test workflow runs once per pushed commit (`concurrency` keyed by the
> SHA), so a docs-only push never cancels a code push's tests. Every job is
> capped: 10 minutes for the classifiers and the reporter, 30 for the
> differential gate and each pytest job.

Same file:182, removed every-PR wording:

> - The in-job gate-relevant path filter is gone: every PR that is not an
>   accepted docs proof runs the full Pyodide differential gate. Before, a PR
>   touching no gate path was trivially green.

Same file:194, removed fork-docs admission claim:

> - A fork's code PR runs no code here: its `gate` fails, saying a maintainer
>   must push the branch to the repository and open a PR from it, which runs the
>   full gate. A fork's accepted docs proof clears as a same-repository one does.

Same file:203, removed target-only Actions policy (the first quoted line
continues the preceding settings list):

> `PINEFORGE_EVIDENCE_BUCKET`). Actions must allow `pull_request_target` (the
> default). The operator recording with `lab gate docs` needs the registry
> connection; only the emergency fallback `--post` also needs a GitHub token
> that may write commit statuses.

Same file:208, removed trust boundary and fork reporter explanation:

> Trust boundary, for whoever reviews the installation PR. The classifier and
> the reporter run trusted code from `main`, for a fork's PR too. `differential`
> and `pytest` run the PR head's code under `pull_request_target` only for a
> same-repository head: both carry `github.event_name != 'pull_request_target'
> || github.event.pull_request.head.repo.full_name == github.repository`, so a
> fork's code never runs in this base-context workflow (a deleted fork counts as
> a fork), while nightly and manual runs stay full. The reporter clears the full
> gate only for a same-repository head; whenever a fork's PR does not pass, the
> description says a maintainer must push the branch here and open a PR from it.
> A fork's run never overwrites a `gate` success that a run of this repository
> (`github-actions[bot]`) posted on the same commit: it leaves it and exits 0, so
> a fork PR sharing a same-repository PR's head shows that PR's pass instead of
> turning it red. Even so, the two jobs hold only `contents: read` (no secrets,
> no caches, no OIDC token, no persisted credentials) and the reporter alone
> holds `statuses: write` without checking out PR code. None of codegen's

Template edit scope and disposition (P3 T2):

- Historical candidate campaign/ci/promote-baseline.yml:21 replaced
  `# Otherwise it exits green without promoting.` with
  `# After promote-guard passes, unmet tree/gate conditions exit green without promoting.`
  Historical candidate lines 32 and 33 added
  `# WIF admits main's workflow for fork PRs too. The credential-free`
  and `# promote-guard refuses their heads before promote can use secrets or OIDC.`
  Those accurate comments exceeded item A's guard/needs-only scope. They
  should be restored or split out. The template tree read during SEC846E
  has restored them. Its remaining proxy synchronization edit is named below.
- campaign/ci/codegen-gate.yml:7, :17, :81, :131, :169, :196,
  :202 and :206 contain item B's authorized pull_request/authz port,
  dependency wiring, removed same-repository job skips, refusal first
  step and guarded reporter.
- The base-checker ref at :91, event adaptations at :117, :216 and :239,
  and AUTHZ_RESULT at :211 and :221 are additional edits that support
  that port. They select the trusted base checker under pull_request
  and keep reporting conditional on authz success, so they are justified
  by item B. They are named explicitly rather than described as only a
  trigger/job insertion. No split is needed for these consequences.
- campaign/ci/promote-baseline.yml:316, :317 and :319 now copy codegen's
  existing process-monitored proxy wait instead of the template's
  30-iteration loop. This is beyond original item A's guard/needs scope,
  but is justified by SEC846E's explicit codegen/template textual-sync
  requirement and preserves codegen main's existing behavior. It need
  not be split under the current item. No codegen proxy edit was made.
  The original difference is visible at :257 in the two origin/main
  files; the final full working-file diff is empty (exit 0).

Job 3: n/a for codegen. Read-only grep of .github, scripts and tests found
no POOL_CLASSES, JOB_RUNNERS, runner_findings, pf-ci-release or pf-gcp-main
pool-label checker. A second git grep across tracked Python, JavaScript,
TypeScript, workflow and shell sources in both codegen and the workflow
tree also found no matches. No engine runner-policy change is made here.

NOT RUN: tests/test_pr_workflows.py, every extracted bash decision and
mutation, Python collection/syntax checks, engine-enabled full pytest,
corpus compilation sweep, Node/npm gates, actionlint, policy checker,
TypeSafe diff guard and live Actions/permission/WIF checks.
COLLECT-ONLY TO RUN: tests/test_pr_workflows.py.

Root handoffs are not edited by this lane. The supervisor installs this
full replacement and REPORT-SEC846.new.md from the reports directory.
No repository code was executed or parsed; no file was deleted, staged
or committed.
