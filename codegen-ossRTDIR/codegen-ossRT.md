# CR-171 — codegen-oss ci-spot runner migration

Branch: `core/cr171-ci-spot`. Starting commit:
`d47397db2bc17996095a19c5b869e58a6a9ce76e`.

Read the dispatcher contract at
`~/.local/state/pineforge-super-top/ci-spot-labels-1018.txt` first. The public
fragment was read with
`git -C ~/code/pf-ci-spot show origin/main:workflows/public.yml`, at
`908569dc04e33c7cdf5733dd65507ddebe71b0e3`.

Five Linux x64 job definitions in four workflows now use the public fragment
verbatim, with indentation adjusted for the job mapping. Each has exactly one
job-level `PF_CI_LABELS: '["self-hosted","pf-ci","pf-ci-x64"]'`. Existing job
environment variables were preserved in their existing env blocks.

## Every workflow and job

In the table, **public fragment** means the exact `runs-on` expression printed
below, including its hosted fallback.

| Workflow | Job | Before runs-on | After runs-on | Routing and hosted exceptions |
| --- | --- | --- | --- | --- |
| `.github/workflows/gate.yml` | `preflight-docs` | `ubuntu-latest` | `ubuntu-latest` (unchanged) | Dependabot PR events reach this job through `pull_request_target`; preserve the explicit Dependabot exception. |
| `.github/workflows/gate.yml` | `differential` | `ubuntu-latest` | `ubuntu-latest` (unchanged) | Same-repository Dependabot PRs satisfy the existing PR guard; preserve the explicit Dependabot exception. |
| `.github/workflows/gate.yml` | `pytest` | `ubuntu-latest` | `ubuntu-latest` (unchanged) | Same-repository Dependabot PRs satisfy the existing PR guard; all Python matrix legs stay hosted. |
| `.github/workflows/gate.yml` | `gate` | `ubuntu-latest` | `ubuntu-latest` (unchanged) | The `always()` reporter also runs for Dependabot PR events; preserve the explicit Dependabot exception. |
| `.github/workflows/test.yml` | `preflight-docs` | `ubuntu-latest` | **public fragment** | Main pushes and manual dispatches route to ci-spot. |
| `.github/workflows/test.yml` | `pytest` | `ubuntu-latest` | **public fragment** | Main pushes and manual dispatches route all existing Python 3.11, 3.12, 3.13 and 3.14 legs to ci-spot. |
| `.github/workflows/publish-pyodide.yml` | `publish` | `ubuntu-latest` | **public fragment** | Version-tag pushes and manual dispatches route to ci-spot. |
| `.github/workflows/release.yml` | `release` | `ubuntu-latest` | **public fragment** | Manual dispatches route to ci-spot. |
| `.github/workflows/promote-baseline.yml` | `promote` | `ubuntu-latest` | **public fragment** | Manual dispatches route to ci-spot; merged-PR `pull_request_target` executions retain `ubuntu-latest` through the fragment's fallback. |

`.github/dependabot.yml` configures Actions, npm and pip update PRs. Because the
brief says to keep jobs that Dependabot runs unchanged, the whole `gate.yml`
file is unchanged, including its scheduled and manual executions. No
`PF_CI_LABELS` entry was added to its hosted jobs.

Both workflows that accept `pull_request_target` also accept
`workflow_dispatch`; there is no workflow triggered only by
`pull_request_target`. Promotion keeps its existing merged-PR guard: an
unmerged PR closure is skipped before the job runs.

There are no macOS, ARM, `*-arm`, arm64 or mixed-OS matrix legs in these five
workflows. Both Python-version matrices remain unchanged.

## Exact public fragment

```yaml
runs-on: >-
  ${{ github.event_name != 'pull_request_target' &&
      contains(fromJSON('["push","pull_request","workflow_dispatch","schedule","release"]'), github.event_name) &&
      (github.event_name != 'pull_request' ||
       (github.event.pull_request.head.repo.full_name == github.repository && github.event.pull_request.head.repo.fork == false)) &&
      fromJSON('["self-hosted","pf-ci","pf-ci-x64"]') || 'ubuntu-latest' }}
env:
  PF_CI_LABELS: '["self-hosted","pf-ci","pf-ci-x64"]'
```

The fragment preserves hosted fallback for fork PRs, `pull_request_target`,
`workflow_run` and every event outside its explicit event list. Spot execution
remains subject to the dispatcher's fixed actor allowlist: `luisleo526` and
`pineforge-app[bot]` as primary actors, plus `github-actions[bot]` for chain
triggers only. No dispatcher policy or actor allowlist was changed.

## Static review and delivery

- All five workflows and all nine job definitions were inspected.
- Each of the five changed jobs was compared with the pinned public fragment;
  its runner expression matches exactly and its job env has one label entry.
- Removing only the runner/env additions restores every workflow byte for
  byte to the starting commit. Steps, triggers, guards, permissions, timeouts,
  matrices and existing environment variables are unchanged.
- `git diff --check` passed.
- No tests, builds, dependency installs, repository scripts or workflows were
  executed on this machine, per the brief. The brief's no-execution rule
  overrides the engine-enabled pytest requirement in `AGENTS.md` for this task.
- One local commit per changed workflow: `test.yml`, `publish-pyodide.yml`,
  `release.yml`, then `promote-baseline.yml` with this report and `DONE`.
- All existing files were retained. Nothing was pushed and no PR was opened.
