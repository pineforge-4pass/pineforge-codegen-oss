# Required engine main touched integration

This describes the source candidate after staged adoption, not installation,
hosted proof or review acceptance.

The canonical PR entry remains `gke-pr.yml` / `pull_request_target`. Its trusted
base admission binds the live same-repository, non-fork, non-draft PR, author,
actor, rerunner and immutable base/head. The new `engine-main` prerequisite calls
`engine-main-touched.yml` from that same trusted commit. Only the existing
`result` job writes `gate`; it still requires all previous prerequisites and
rechecks live identity before publishing to the event head.

The shared workflow contains `changes`, `native` and a dispatch-only `publish`.
`workflow_dispatch` supplies a write-authorized public-input proof route through
those same jobs and the same driver. It can test a draft/test branch's immutable
public commits, but grants no PR admission or status-writing authority.
There is no separate advisory aggregate to configure as required.

It also handles **every push to main**, with no path filter or concurrency
group. Each event tests its exact `after` SHA against its `before` tree even
after main advances again. Push admission binds
`github.sha == event.after == workflow_sha` without querying a later main tip.
Docs-only main pushes still build ENGINE MAIN and execute smoke. Normal native
job check runs therefore attach to the actual pushed SHA, including a merge.

## Selection and identity

The trusted definition supplies `scripts/ci_engine_main_touched.py`. The
candidate checkout supplies Git objects as data during selection; its Python,
actions and scripts are never executed by `changes`. The driver verifies its
own definition SHA and the tested checkout SHA independently.

For PRs, `git diff --name-status -z --find-renames BASE...HEAD` retains additions,
deletions and both rename paths. The diff's merge-base inventory is merged with
head data: head data can add targets or requirements, but cannot erase native
calls, remove base targets, or invent non-public exclusions. A removed native
test stays selected and fails collection rather than silently losing coverage.
A removed pure unit test alone does not require native work.

Native paths include:

- `pineforge_codegen/codegen/**` and `analyzer/**`, including every `emit_*`,
  checked-settings, factory and creation path;
- explicitly listed shared grammar, typing, binding, diagnostic and emission
  stages, plus unknown product modules conservatively;
- C/C++ sources, headers and templates, including generated golden C++;
- the native compile/replay/legacy-C++ harnesses, host-member generator, native
  CI controls, and touched tests with native dependencies at base or head.

`request_feed_inventory.py` (metadata computed after generation) and
`timeframe_token_cli.py` (JSON transport) are the two documented product exceptions.
Docs and ordinary pure unit **PR** changes visibly report `SKIP native`. Missing,
malformed or unreadable input fails selection instead of producing a skip.
Main uses a direct before/after diff and always selects smoke, regardless of
triggers. A zero before SHA is an initial push; a missing nonzero before object
is fetched by that immutable SHA rather than a moving branch name.

Selection uses explicit function or class-method selectors: changed test
files, tests importing changed modules/harnesses, matching module topics,
generated-golden tests and constructor/lifecycle tests. This static mapping is
reviewable but is not a complete impact graph. Every native selection includes
the unchanged checked-construction regression (both legacy parameters), minimal
compile smoke, checked-settings metadata/wrappers, and checked-settings runtime
values/receipt (both optimization parameters). No expected-outcome or old-head
SHA is an input to the implementation.

Known non-public cases identified at the diff baseline are excluded and listed
by nodeid and input name. A new/unrecognized missing input causes a selected
case to skip and fails the audit. Public historical comparison targets remain
selected; both codegen checkouts fetch full history. Public corpus and LFS feed
bootstrap happens only if a selected case requires it.

The decision records definition SHA/tree, codegen base, head SHA/tree, diff
baseline, changed paths, targets and reasons. Its digest binds all of these plus
worker count. The native job recomputes that plan with the same trusted driver,
requires the digest to match, and emits `executed_plan` only after successful
ordinary execution. The canonical result consumes the actual reusable job,
changes and native results. An emitter diff requires native success and the
matching executed digest. A non-native decision requires changes success,
matching base/head/definition, a valid tree/digest and native job-level `skipped`.
Missing outputs, wrong identities, failure, cancellation, unexpected skip or a
native result with an unexecuted plan cannot produce a successful `gate`.

## Native bootstrap

All jobs use standard `ubuntu-24.04`. Native execution gets no secrets, OIDC,
environment, Actions cache or persisted checkout credentials. It checks out
public `pineforge-engine` **main at runtime**, verifies HEAD against the freshly
fetched `origin/main`, and records engine SHA/tree separately from codegen and
the workflow definition. This forward integration is separate from exact
release pairing.

The driver installs only pytest/xdist and performs a fresh Release/source-layer
build:

```sh
cmake -S engine -B engine/build -DCMAKE_BUILD_TYPE=Release \
  -DPINEFORGE_BUILD_TESTS=OFF -DPINEFORGE_BUILD_SOURCE_LAYER=ON \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++
cmake --build engine/build --parallel 8
```

One numeric worker parameter, restricted to 1–8, drives both build and pytest.
PRs use 8; dispatch offers 1, 2, 4 and 8. There is no authored job timeout.
Before importing product code or a harness, the driver requires the real engine
headers, generated version header, nonempty static archive, Eigen and compiler,
then exports explicit native paths. A real compiler preflight requires the
checked-settings API macro, keeping the regression's guarded assertions active.

pytest runs the selected targets once. The audit retains nodeids and phase/call
outcomes. Zero collection, any missing target or construction parameter, a case
without an ordinary call, skip/xfail, or a failed phase with a zero pytest exit
fails closed. Real build/compiler/pytest exits remain failures. Logs carry the
three independent identities; artifacts retain selection, install/build/
preflight/pytest logs, execution details and JUnit when execution reaches it.
Setup failure or absent JUnit is never assertion evidence.

PR scope is touched+smoke. Full engine-enabled pytest, including the corpus
gate, remains required once on the final merged `main` tree on an approved
worker. That full-main execution is an operator obligation, not a new behavior
silently added to `test.yml` or the old Python/Pyodide jobs.
Every main push additionally runs this native touched+smoke job; it does not
add another broad full-suite run.

## Historical source checks

A dispatch's normal job checks belong to the newer definition commit. For
native dispatches only, the isolated `publish` job has `contents: read`,
`actions: read` and `checks: write` and checks out only that trusted definition.
The reusable caller declares this permission ceiling, while the callee's
`changes` and `native` explicitly reduce to `contents: read`. No write token
reaches candidate Python/build/tests. PR execution never invokes publication,
and no job here writes the `gate` status.

The publisher reauthorizes public inputs and reads the exact run attempt and
completed `Engine main native` job from GitHub's API. Definition SHA, run ID,
branch, job result and job details URL must match. Trusted changes identities,
source event and plan must bind actual native outputs. Success additionally
requires matching completed/executed plans, zero audit errors/pytest exit and
both ordinary construction rows passed. An ordinary failed execution produces
failure; setup/incomplete execution produces `action_required`; cancellation
produces `cancelled`; missing/mismatched state refuses publication. None of
these may become native success or claimed assertion RED.

The check `engine-main-native-source` is created on the tested codegen SHA and
read back to verify `head_sha`, conclusion and details URL. Details link the
actual native job. Summaries report observed outcomes; no input supplies an
expected result and no literal SHA selects one. ROOT must still read raw
build/audit logs to establish the required two-assertion RED.

## Staged source adoption

Main owns admission. The final checker admits exactly the new inventory and
pins the entire reusable workflow and native driver to the installed base
source. It rejects omission of `engine-main`, a different call route, callee or
implementation. This final tree deliberately contains no legacy fallback.

CORE/ROOT must first install a separately reviewed bootstrap policy on `main`
before activating the caller shape. The source-only bootstrap delta is:

1. Install the final driver/checker/publisher body, but stage the shared workflow
   without its exact two-line main `push` trigger. Main-push activation waits.
   Keep `gke-pr.yml` byte-for-byte at the pre-integration shape. Register the
   dispatch workflow on the default branch; verify operator routing and isolated
   checks-write permission separately.
2. Temporarily admit exactly two canonical inventories: the old six
   prerequisites/result graph and the final graph including `engine-main`.
   For the old inventory keep every original command, checkout count/ref,
   identity env, six `result.needs`, and old reporter predicate exact.
   For the new inventory use every final strict call/dependency/output rule.
   No mixed graph, arbitrary job set or generic local-action exemption is valid.
   Pin the workflow/driver body in both branches. Temporarily allow only the
   unchanged inactive-push header with the old caller, or the exact main-only
   push header with the new caller. No path filters, cancellation group, altered
   body or mixed caller/push activation is admitted.
   Test both complete shapes and mutations on approved workers.
3. Run original PR RED, merged-main RED, fixed GREEN and docs PR SKIP using one
   immutable published branch definition with this native implementation.
   Obtain genuine review. Then activate the final canonical and main-push
   workflows and replace the bootstrap checker/controls with
   this final strict source together. The old inventory/report predicate must
   disappear in this activation change. Publication remains CORE-controlled.

The bootstrap source must preserve its original six-leg reporter only while
the old caller is installed; otherwise it would strand old PR runs. It cannot
be left installed after activation. This description prepares source; it
authorizes no push, merge, bypass or branch-protection change. The scratch
source report names the exact checkpoint paths for constructing these deltas.

## Hosted proof requests

GitHub must register the dispatchable workflow on the default branch first.
A branch-only YAML file does not prove that its definition executed.
Use the same published branch `DEFINITION_REF`, held at one immutable commit,
for all requests:

```sh
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f source_event=pull_request \
  -f codegen_base_ref=8d615dd27b31acd4fe92b57be7c1811cc42ecbda \
  -f codegen_head_ref=106817c2d3838c94f19f0e1a851dfd217711c806 -f workers=8
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f source_event=push_main \
  -f codegen_base_ref=8d615dd27b31acd4fe92b57be7c1811cc42ecbda \
  -f codegen_head_ref=8d1b254e4868a7869dd9da8b4fee2d922445b414 -f workers=8
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f source_event=push_main \
  -f codegen_base_ref=FIX_BASE_SHA -f codegen_head_ref=FIXED_HEAD_SHA -f workers=8
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f source_event=pull_request \
  -f codegen_base_ref=74fe4cd31f704178dc9a8461710970f82929e363 \
  -f codegen_head_ref=df2b7c057b5a31edf76bd918515f339f2fa93aa4 -f workers=8
```

PR343's full original base is `8d615dd27b31acd4fe92b57be7c1811cc42ecbda`;
its old head's parent is a narrower delta and must not replace it. The fixed
head remains external. The supplied docs-only pair is provenance, not a SKIP
result. Local Git identifies the merge's parent as that same base and its tree
as `a98167c3c33176b4e131a9125204c48ac44b819f`; neither establishes runtime behavior.
RED must show **both existing construction assertion failures after a successful
real build and preflight**. GREEN must execute those same ordinary cases from
the independently fixed head. SKIP needs a genuine docs diff and a visible
native job-level skip. Compare definition/codegen/engine identities for each
run; engine main may advance between them. All proof requests here are UNRUN
by the source author. Source, unit controls and prechecks establish no hosted
outcome or process acceptance.

CORE must additionally read the GitHub check-run API record on
`8d1b254e4868a7869dd9da8b4fee2d922445b414`: completed failure with details linked
to the real completed native job. A definition-commit check or the old PR-head
check cannot replace it. After activation, verify genuine main pushes,
including docs-only pushes, execute native on their own event SHAs without
cancelling earlier pushes. No protected-main rewind or synthetic push is used.
