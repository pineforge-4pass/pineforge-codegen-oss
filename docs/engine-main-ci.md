# Required engine main full suite and PR touched integration

**Bootstrap checkpoint:** this commit registers the shared proof workflow and stages its trusted admission rules. The required PR-native prerequisite and automatic full-native main-push trigger are inactive here. Descriptions of those behaviors below describe the following activation target; they are not a claim that this bootstrap has installed or executed them. Activation still requires the real hosted proofs, input provisioning and review.

This describes the source candidate after staged adoption, not installation,
hosted proof or review acceptance.

The canonical PR entry remains `gke-pr.yml` / `pull_request_target`. Its trusted
base admission binds the live same-repository, non-fork, non-draft PR, author,
actor, rerunner and immutable base/head. The new `engine-main` prerequisite calls
`engine-main-touched.yml` from that same trusted commit. Only the existing
`result` job writes `gate`; it still requires all previous prerequisites and
rechecks live identity before publishing to the event head.

The shared workflow contains `changes`, `native`, the actual dependency
`outcomes` bridge and a dispatch-only `publish`.
`workflow_dispatch` supplies a write-authorized public-input proof route through
those same jobs and the same driver. It can test a draft/test branch's immutable
public commits, but grants no PR admission or status-writing authority.
There is no separate advisory aggregate to configure as required.

It also handles **every push to main**, with no path filter or concurrency
group. Each event tests its exact `after` SHA against its `before` tree even
after main advances again. Push admission binds
`github.sha == event.after == workflow_sha` without querying a later main tip.
Docs-only main pushes still build ENGINE MAIN and execute the full repository
suite once. Normal native
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
Main uses a direct before/after diff for provenance and always selects full mode,
regardless of triggers. Its pytest target is the entire `tests` directory.
Neither changed paths nor the PR selector determine main targets. A zero before
SHA is an initial push; a missing nonzero before object
is fetched by that immutable SHA rather than a moving branch name.

PR selection uses explicit function or class-method selectors: changed test
files, tests importing changed modules/harnesses, matching module topics,
generated-golden tests and constructor/lifecycle tests. This static mapping is
reviewable but is not a complete impact graph. Every native selection includes
the unchanged checked-construction regression (both legacy parameters), minimal
compile smoke, checked-settings metadata/wrappers, and checked-settings runtime
values/receipt (both optimization parameters). No expected-outcome or old-head
SHA is an input to the implementation.

PRs exclude known non-public cases identified at the diff baseline and list
them by selector and input name. Full main mode excludes no cases: it retains
private/platform-dependent rows in the existing suite, reports their actual
nodeids and reasons, and treats any omissions as incomplete execution. No
private substitute is supplied. These provisioning NEEDS prevent a literal
whole-repository GREEN; a passing public subset is never described as full
coverage. Unknown missing inputs also fail the audit.

Both codegen checkouts fetch full history. Literal/constant historical refs
discovered without importing candidate code are resolved to genuine objects
before collection; missing full refs are fetched by immutable ID. Unavailable
abbreviated refs remain NEEDS, never a clone of the current candidate.
The runtime audit catches additional dynamically computed requirements.
Public corpus/LFS feed bootstrap is unconditional in full mode and conditional
on selected requirements in PR mode.

The decision records definition SHA/tree, codegen base, head SHA/tree, diff
baseline, changed paths, mode, targets and reasons. Main additionally binds the
head tree's complete static test/module inventory and named input requirements.
There is no constant expected test count. Its digest binds all of these plus
worker count. The native job recomputes that plan with the same trusted driver,
requires the digest to match, and emits `executed_plan` only after successful
ordinary execution. The canonical result consumes the actual reusable job,
changes and native results. An emitter diff requires native success and the
matching executed digest, touched mode and a complete actual inventory.
A non-native decision requires changes success,
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

pytest runs once: PR touched+smoke, or all repository tests on the exact pushed
main tree. Full mode includes existing compile, emitted runtime, corpus and
history cases without a separate smoke pass. It provisions clang, real public
strategies/LFS replay feed and real historical objects before collection.
No full-main run is deferred to a later manual operator.

The trusted audit plugin runs in each xdist worker and in its controller.
Artifacts retain every worker's pre-selection items, final items, explicit
deselections and collection reports, including module-level skips, alongside
the actual setup/call/teardown reports. The audit compares those inventories,
the bound static selectors/modules, and actual ordinary calls. An omitted
native parameter, zero collection, missing phase, skip/xfail, setup/teardown
failure or unsuccessful collection prevents full success. A real failing call
remains a failure even if pytest were to return zero. No count is supplied as
an expected result.

`execution.json` binds this inventory and its digest to the definition, engine
and codegen identities and records actual exits, omissions and input NEEDS.
Only complete successful full execution emits `full_executed_plan`; a main
native success requires it. Real build/compiler/pytest exits remain failures.
Logs retain selection, install/build/history/corpus/preflight/pytest details and
JUnit when reached. Setup failure or absent JUnit is never assertion evidence.
The old Python/Pyodide jobs retain their existing scope.

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
requires the event's correct mode, actual inventory digest, engine SHA/tree,
matching completed/executed plans, zero audit errors/pytest exit, complete
coverage and both ordinary construction rows passed. Main success additionally
requires `full_success=true` and the matching `full_executed_plan`; two passing
construction rows plus a matching selection digest are insufficient.
An ordinary failed execution produces
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
3. Run original touched PR RED, merged-main **full** RED, restored-main **full**
   GREEN and docs PR SKIP using one
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
  -f codegen_base_ref=8d1b254e4868a7869dd9da8b4fee2d922445b414 \
  -f codegen_head_ref=e32e9e4b98ec23581fb55966ee295443c968a0b0 -f workers=8
gh workflow run engine-main-touched.yml \
  --repo pineforge-4pass/pineforge-codegen-oss --ref DEFINITION_REF \
  -f source_event=pull_request \
  -f codegen_base_ref=74fe4cd31f704178dc9a8461710970f82929e363 \
  -f codegen_head_ref=df2b7c057b5a31edf76bd918515f339f2fa93aa4 -f workers=8
```

PR343's full original base is `8d615dd27b31acd4fe92b57be7c1811cc42ecbda`;
its old head's parent is a narrower delta and must not replace it. Published restored main
`e32e9e4b98ec23581fb55966ee295443c968a0b0` (tree
`db750e4704781cdea7ab1cd19d79fb1c48baf701`, parent `8d1b254e4868a7869dd9da8b4fee2d922445b414`)
is restored-source provenance, not executed GREEN. The supplied docs-only pair
is provenance, not a SKIP
result. Local Git identifies the merge's parent as that same base and its tree
as `a98167c3c33176b4e131a9125204c48ac44b819f`; neither establishes runtime behavior.
RED must show **both unchanged construction assertion failures after a
successful real build and preflight**, in the same hosted full runner used for
restored main. GREEN must demonstrate complete full execution and those same
ordinary cases from the independently restored head. A private/platform NEEDS,
missing call, setup failure or public-only pass does not satisfy full GREEN.
SKIP needs a genuine docs diff and a visible
native job-level skip. Compare definition/codegen/engine identities for each
run; engine main may advance between them. All proof requests here are UNRUN
by the source author. Source, unit controls and prechecks establish no hosted
outcome or process acceptance.

CORE must additionally read the GitHub check-run API record on
`8d1b254e4868a7869dd9da8b4fee2d922445b414`: completed failure with details linked
to the real completed native job. A definition-commit check or the old PR-head
check cannot replace it. After activation, verify genuine main pushes,
including docs-only pushes, execute full mode on their own event SHAs without
cancelling earlier pushes. No protected-main rewind or synthetic push is used.
Focused mode/inventory/missing-input controls run on Spark2, followed by the
same hosted definition's actual outcomes, source check API readback and a
genuine final Claude REV. All remain UNRUN here; source GO and G01 closure
require that execution and review evidence.
