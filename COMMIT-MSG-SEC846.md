ci: guard baseline promotion before credential jobs (SEC846)

PARKED 10-20 17:35 (owner #859): CI security work resumes 10-23 10:00;
committed as-is, no PR. Review read 12 returned CHANGES NEEDED (P2 guard
text differs across engine, codegen and the template; P3 handoff quotes
omitted, template edits beyond scope) — open, not fixed.

Require promote-guard before the baseline promotion job can receive App
secrets or an OIDC credential. Every PR close first requires this repository's
non-fork head and the canonical author association/bot/permission rule.
Copy the current canon's author permission fallback and its comments and
refusal verbatim: a private-member CONTRIBUTOR proceeds only with
admin/maintain/write from GitHub. A merged PR additionally requires an
allowlisted merger or its own successful admin/maintain/write lookup.
Failed, empty and non-write lookups refuse. An authorized own unmerged
close leaves promotion skipped; manual dispatch remains allowed.

Pin the complete guard variant independently in tests/test_pr_workflows.py.
Extend the real-script bash decision table with separate gh replies per
author and merger username, including a writer CONTRIBUTOR merged by a
writer, refused author permissions or lookup errors, and a writer author
merged by a reader. Author mutations delete its lookup or make a failed
lookup pass; deleting the merger lookup must also be caught independently.
Retain mutations for every identity field, a no-op fail, removed
repository/fork checks, an expanded author or bot allowlist, non-write
permissions, an unguarded credential job, duplicate job-key overrides,
and secrets, OIDC or checkout added to the guard. All tests are NOT RUN here.

CONTRIBUTING.md sentences (verbatim; each contains at least six words):

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

NOT RUN: .github/workflows/promote-baseline.yml, tests/test_pr_workflows.py,
CONTRIBUTING.md, COMMIT-MSG-SEC846.md and REPORT-SEC846.md. The lane forbids
executing or parsing repository code, including tests and workflow linters.
The engine-enabled pytest suite, corpus sweep, policy checker and TypeSafe
diff guard remain for the supervisor. No commit or push was performed.
