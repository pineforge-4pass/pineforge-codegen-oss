# SEC846 — codegen baseline promotion

Corrected full handoff for the pending merge and SEC846E follow-up.
All tests and repository scripts: **NOT RUN**. Source inspection and
awk/md5 measurements are the only verification recorded here.

## PR BODY

Make the credential-free promotion guard identical to the engine lane's
final reference and the workflow template, then pin that entire block with
one independent literal. The nested author case, comments and permission
fallback remain verbatim canon. The guard uses ubuntu-latest, empty job
permissions, no repository secrets, no checkout and no OIDC grant.

`.github/workflows/promote-baseline.yml:108` is the complete guard.
The unchanged triggers are closed `pull_request_target` at line 74 and
`workflow_dispatch` at line 79. The credential job needs only the guard at
line 167 and keeps its existing condition at line 168:

```yaml
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true }}
```

There is no status function to bypass a failed dependency. Workflow-level
`id-token: write` remains at line 105; empty guard permissions at line 111
prevent inheritance. The App secrets remain at line 272
(`secrets.PINEFORGE_APP_ID`) and line 273
(`secrets.PINEFORGE_APP_PRIVATE_KEY`).
GCP authentication remains at line 295, with the existing provider and
service account at lines 297 and 298. Credential steps are unchanged.

Every PR close first checks repository equality at line 136 and an exact
false fork flag at line 137, including unmerged closes. Authors outside
OWNER/MEMBER/COLLABORATOR and the dependabot[bot]/pineforge-app[bot]
allowlist need admin/maintain/write from the author lookup at line 147.
A failed lookup yields none; empty or non-write replies refuse.
An authorized own unmerged close exits successfully at line 152 with
`promote-guard: unmerged pull request; promotion is skipped`, and promote's
condition skips credential work. A merged close requires a nonempty
merger at line 153 and either an allowlisted login or its own
admin/maintain/write lookup at line 159. Author permission cannot substitute
for merger permission. Dispatch passes; other events refuse.

Exact refusal templates:

```text
pull request from another repository (${HEAD_REPO:-unknown})
pull request from a fork
author ${PR_AUTHOR:-unknown} (${ASSOCIATION:-NONE}) has permission '$perm', not write, and is not an allowlisted bot
pull_request_target merger is empty
pull_request_target merger ${MERGED_BY:-unknown} has permission '$perm', not write
event $EVENT does not run baseline promotion (only pull_request_target and workflow_dispatch are allowed)
```

Each refusal exits 1, prints `::error title=authz::$1` and appends
`authz: REFUSED: $1` to GITHUB_STEP_SUMMARY. A failed author or merger lookup,
including a failed command that prints write, fails the exact permission
case; it never defaults to an allowed permission.

The full-block MD5 measured in each readable tree is:

| Tree | Guard MD5 |
| --- | --- |
| engine | `933cc1f869787b37f3b339978d3828a2` |
| codegen | `933cc1f869787b37f3b339978d3828a2` |
| workflow template | `933cc1f869787b37f3b339978d3828a2` |

The literal at `tests/test_pr_workflows.py:137` has the same hash. Its
comparison at line 216 includes the header, all comments, every script
line and the blank separator. There is no per-repository difference in
the guard. The author cases in canon, workflow and literal all measure
`f684468222dd2d9915868e54fee8accc`.

New real-workflow mutations at test lines 606, 611, 616 and 621 alter the
header, step text, bot comment and separator respectively. They require
one real match and changed workflow text before the independent literal
comparison must refuse it. Existing mutations still cover every identity
field, head/fork refusal, author and merger lookups, lookup failure,
no-op fail, script replaced by exit 0, removed dependency, status bypass,
changed triggers, guard secrets/checkout/OIDC and duplicate job-key
overrides.

The real-script decision harness starts at test line 443; the run block
is extracted at line 435. CI will execute that script under bash with
only gh stubbed, returning distinct author/merger fixtures by username.
The harness asserts literal expected exits, exact annotation/summary
messages and ordered lookup endpoints. Missing bash fails explicitly
with NOT RUN. The unmerged summary now matches the reference. Merger
mutation targets use its exact new indentation and wording.

Authored cases retained, all **NOT RUN**:

| Case | Literal expected exit |
| --- | --- |
| Foreign head, merged or unmerged; same-repo fork flag | 1 |
| Missing head or fork flag | 1 |
| Unknown/NONE author without write | 1 |
| CONTRIBUTOR author with admin/maintain/write, writer merger | 0 |
| Reader, empty or failed author lookup | 1 |
| Writer author, reader merger | 1 |
| Reader/triage/empty/failed merger lookup, or empty merger | 1 |
| Own authorized unmerged member or writer CONTRIBUTOR close | 0 |
| Allowlisted bot author, writer merger; allowlisted bot merger | 0 |
| Manual dispatch | 0 |
| push, workflow_run or unknown event | 1 |

The credential condition requires merged true on the PR path but does
**not** require base main. The trigger has no branches filter. Every
base-ref use remains:

```text
.github/workflows/promote-baseline.yml:194:          BASE_REF: ${{ github.event.pull_request.base.ref || 'main' }}
.github/workflows/promote-baseline.yml:198:          git fetch --quiet origin "$BASE_REF" "+refs/pull/$PR_NUMBER/head:refs/remotes/origin/pr-head"
.github/workflows/promote-baseline.yml:199:          if ! git merge-base --is-ancestor "$MERGE_SHA" "origin/$BASE_REF"; then
.github/workflows/promote-baseline.yml:200:            echo "::notice::merge commit $MERGE_SHA is not on origin/$BASE_REF; skipping"
```

No base-ref condition was added.

WIF was reread without network from
`/Users/haoliangwen/code/pineforge-workflow`, origin/main:
`terraform/gcp/github-wif.tf:130`, :131, :132, :133 and :134 require
the configured repository owner ID, listed promotion repository IDs,
listed promotion repository names, refs/heads/main, and the exact
repository/.github/workflows/promote-baseline.yml@refs/heads/main
identity. The path/ref are defined at lines 69 and 81.
`docs/VERIFY.md:1246` describes the same main-copy requirement.

A target event runs the base repository's main workflow with
GITHUB_REF=refs/heads/main, including for a fork-headed PR. Those claims
satisfy WIF too; the guard is the layer that distinguishes that fork head
before credential use. The promotion service account's blast radius,
`docs/VERIFY.md:1244` and :1245, is Cloud SQL client on the registry
instance, accessor on the registry-db secret, and read on the evidence
bucket. Terraform and IAM were not edited or checked live.

Codegen's accepted gate pattern is preserved from merged main:

```text
.github/workflows/gate.yml:7:  pull_request_target:
.github/workflows/gate.yml:29:          ref: ${{ github.sha }}
.github/workflows/gate.yml:30:          path: .docs-gate
.github/workflows/gate.yml:31:          persist-credentials: false
.github/workflows/gate.yml:37:          ref: ${{ github.event.pull_request.head.sha }}
.github/workflows/gate.yml:38:          path: .candidate
.github/workflows/gate.yml:39:          persist-credentials: false
.github/workflows/gate.yml:54:          checker=.docs-gate/.github/pineforge/ci/codegen-docs-check.mjs
.github/workflows/gate.yml:56:            node "$checker" "$PWD/.candidate" "$BASE_SHA" "$HEAD_SHA" "$BASE_BRANCH" > "$RUNNER_TEMP/codegen-docs-diff.json"
.github/workflows/gate.yml:139:    permissions:
.github/workflows/gate.yml:140:      statuses: write
```

The head checkout is data; the trusted checker comes from the base
workflow checkout. There are no repository-secret references in gate.yml.
Its changes already staged by the supervisor's main merge are retained;
SEC846E does not edit that workflow.

## MERGE AND P3 PUBLICATION RECORD

The supervisor's merge is still in progress. CONTRIBUTING.md:112 retains
the SEC846 paragraph and :131 retains main's npm operator guidance, through
the trusted-publisher/environment explanation at :177. This marker-free
union was already present at lane start. The index still lists the file
unmerged; staging and merge completion belong to the supervisor.
No main documentation, tests or features are removed.

Codegen's documentation change adds the quoted guard paragraph; it removes
no doc text. The corrected commit handoff includes that paragraph verbatim
and the omitted cross-repository removed quotations:

| Historical source revision / file:line | Removed material |
| --- | --- |
| engine `0212cabe2d87e64b9656791ade118be0bdee01bc:docs/ci.md:768`, especially :771, :773, :775 | Promotion main/head/tree wording and unconditional green-exit claim |
| same source :1059 | “Promotion stays hosted for both target and dispatch triggers.” |
| workflow `6d8a82791e87992b64ede831547f12fef66df76f:docs/VERIFY.md:1187` | “Fork PRs never could” and write-token explanation |
| same revision `docs/CODEGEN-DOCS-GATE.md:140`, :144 | Previous-template identity and engine-needs-no-update claims |
| same file :153, :167, :171 | Target-trigger trust, Actions event policy and PR pytest wording |
| same file :175, :182, :194 | Universal timeout claim, every-PR wording and fork docs proof admission |
| same file :203, :208 | Target-only Actions policy and old fork/trusted-reporter boundary |

Template scope is explicit. Historical candidate
`campaign/ci/promote-baseline.yml:21` changed the green-exit comment; :32
and :33 added WIF/guard comments outside item A. Those comments should be
restored or split out, as READ-CODEX12 T2 requests. The currently readable
template has restored them. Its additional proxy synchronization is named below.

`campaign/ci/codegen-gate.yml:7`, :17, :81, :131, :169, :196, :202 and
:206 implement item B's authorized pull_request/authz/dependency/refusal
port. Additional base-checker ref :91, event adaptations :117/:216/:239
and AUTHZ_RESULT wiring :211/:221 are justified consequences of that
port, selecting the trusted base checker and retaining fail-closed
reporting. They need not be split, but must be listed rather than omitted
from the handoff. Other repositories were read only.

The template also synchronizes its proxy wait at
`campaign/ci/promote-baseline.yml:316`, :317 and :319 with codegen's
existing process-monitored loop. This exceeds original item A, but is
justified by SEC846E's explicit codegen/template textual-sync requirement;
it preserves codegen main's behavior and needs no split under this item.
The pre-existing origin/main difference at :257 was observed earlier.
The latest full working-file diff is empty, exit 0. No codegen proxy
change was made by this lane.

Job 3: **n/a**. Read-only grep of codegen .github, scripts and tests for
POOL_CLASSES, JOB_RUNNERS, runner_findings, pf-ci-release and pf-gcp-main
returned no matches. A second git grep across tracked Python, JavaScript,
TypeScript, workflow and shell sources in both codegen and the workflow
tree also found no matches. Neither has this pool-label checker to extend.

## NOT CHANGED

- Promotion trigger types/inputs, credential condition and runner, App
  token scope/secrets, workflow OIDC grant, authentication, checkout,
  tree/gate checks, proxy and promotion steps.
- All main merge changes, including its instruction files, legal text,
  version, runtime features and release tests; none are authored by this
  follow-up. No instruction file is edited by this lane.
- Engine/template files and root handoffs. Supervisor installs these full
  replacement handoffs from the codegen reports directory.
- No file was deleted, staged or committed; no branch/history changed.

## DECISION

- Use promote-guard because canon authz refuses target events; take the
  engine lane's final full block without repository-specific edits.
- Keep the canon author case byte for byte and independently authorize
  the merger after the authorized-unmerged exit.
- Preserve the credential job's existing condition and all other
  workflow conditions; no runner or credential migration is part of this.
- Pin the entire job literally, including comments and its separator;
  real mutations and extracted-script decisions supply independent
  negative controls when CI executes them.
- Keep main's marker-free union and let the supervisor resolve the index.
- Keep codegen's accepted gate pattern; item B applies in the template lane.
- Restore/split unrelated template promotion comments; name the justified
  gate-port consequences and template proxy synchronization.
- Follow the explicit no-execution lane rule. AGENTS.md's required
  engine-enabled suite and corpus sweep remain NOT RUN.

## COLLECT-ONLY TO RUN

- `tests/test_pr_workflows.py`: **NOT RUN**. Reread all additions as text;
  no Python parser, collection or import was invoked.

## NOT RUN

| Test, script or check | Status |
| --- | --- |
| tests/test_pr_workflows.py, all decisions and mutations | NOT RUN |
| Python syntax checks and pytest collect-only | NOT RUN |
| Engine-enabled `python -m pytest -ra` | NOT RUN |
| `python -m pytest -ra tests/test_compile_corpus.py` | NOT RUN |
| Node/npm selftest, full/differential gate and audit | NOT RUN |
| actionlint, ShellCheck and workflow parsing | NOT RUN |
| ci-policy-check.py and TypeSafe diff guard | NOT RUN |
| GitHub Actions, live permission lookup and WIF/IAM readback | NOT RUN |

## NOT CHECKED

Test collection/execution, generated C++ behavior, engine/corpus parity,
real GitHub event payloads, deployed fork settings, runner routing,
permissions and IAM, policy/TypeSafe acceptance and merge completion.
