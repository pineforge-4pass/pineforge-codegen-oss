"""Pin #789/#808/#819/#838/#839/#840 authz, pool routing and credential exceptions."""
import os
import re
import subprocess
from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
CONCURRENCY_RULES = {
    "test.yml": ("test", "github.event_name == 'pull_request'"),
    "gate.yml": ("gate", "github.event_name == 'pull_request'"),
}
CI_RUNNER = '["self-hosted","pf-ci","pf-ci-x64"]'
AUTHZ_RUNNER = '["self-hosted", "pf-ci", "pf-ci-x64"]'
# Verbatim job from ci-labels.md, "The `authz` job" (R789K,
# 2026-10-20 16:02). This literal must stay independent of the workflow text.
AUTHZ_JOB = """  authz:
    name: authz
    # Pool label (needs no toolchain or Docker). Switch to pf-ci-crj once laneboss's
    # bulletin says the Cloud Run class is live.
    runs-on: ["self-hosted", "pf-ci", "pf-ci-x64"]
    permissions: {}
    # No timeout-minutes: the owner's no-time-limits rule (check-no-time-limits refuses new ones).
    steps:
      - name: Only our own pull requests and our own events run CI
        env:
          EVENT: ${{ github.event_name }}
          REPO: ${{ github.repository }}
          HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}
          HEAD_IS_FORK: ${{ github.event.pull_request.head.repo.fork }}
          ASSOCIATION: ${{ github.event.pull_request.author_association }}
          PR_AUTHOR: ${{ github.event.pull_request.user.login }}
          WR_HEAD_REPO: ${{ github.event.workflow_run.head_repository.full_name }}
          WR_ACTOR: ${{ github.event.workflow_run.triggering_actor.login }}
          GH_TOKEN: ${{ github.token }}   # read-only use: one permission lookup for workflow_run
          # Bot logins whose same-repository pull requests count as ours: dependabot[bot] (CI-DEPBOT A)
          # and the org's own GitHub App bot logins by name (space-separated), nothing else.
          ALLOWED_BOTS: "dependabot[bot] pineforge-app[bot]"
        run: |
          set -eu
          fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }
          case "$EVENT" in
            push|workflow_dispatch|schedule|merge_group|release|workflow_call|repository_dispatch)
              # Only someone with write access to this repository (or our App's token) can cause these
              # events; none of them runs pull-request code.
              ;;
            workflow_run)
              # The pwn-request path: it runs in THIS repository with secrets even when the triggering run
              # came from a fork. Refuse ALWAYS unless the triggering run's head is this repository, and
              # apply the same identity rule to its triggering actor as to a pull-request author.
              [ "$WR_HEAD_REPO" = "$REPO" ] || fail "workflow_run triggered from another repository (${WR_HEAD_REPO:-unknown})"
              case " $ALLOWED_BOTS " in
                *" $WR_ACTOR "*) ;;
                *)
                  # No author_association exists on workflow_run: ask GitHub for the actor's permission on
                  # this repository; anything but admin/maintain/write (or a failed call) refuses.
                  perm=$(gh api "repos/$REPO/collaborators/$WR_ACTOR/permission" --jq .permission 2>/dev/null || echo none)
                  case "$perm" in admin|maintain|write) ;; *) fail "workflow_run triggering actor ${WR_ACTOR:-unknown} has permission '$perm', not write" ;; esac ;;
              esac ;;
            pull_request)
              [ "$HEAD_REPO" = "$REPO" ] || fail "pull request from another repository (${HEAD_REPO:-unknown})"
              [ "$HEAD_IS_FORK" = "false" ] || fail "pull request from a fork"
              case "$ASSOCIATION" in
                OWNER|MEMBER|COLLABORATOR) ;;
                *)
                  case " $ALLOWED_BOTS " in
                    *" $PR_AUTHOR "*) ;;
                    *)
                      # A member whose org membership is private shows as CONTRIBUTOR: ask GitHub for the
                      # author's permission on this repository; anything but admin/maintain/write (or a
                      # failed call) refuses.
                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo none)
                      case "$perm" in admin|maintain|write) ;; *) fail "author ${PR_AUTHOR:-unknown} (${ASSOCIATION:-NONE}) has permission '$perm', not write, and is not an allowlisted bot" ;; esac ;;
                  esac ;;
              esac ;;
            *) fail "event $EVENT does not run CI (pull_request_target and other events are not allowed)" ;;
          esac
          echo "authz: allowed ($EVENT${PR_AUTHOR:+ by $PR_AUTHOR})" >> "$GITHUB_STEP_SUMMARY"
"""
FORBIDDEN_FORK_GUARD = (
    "github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository"
)
AUTHZ_SUCCESS = "needs.authz.result == 'success'"
# R5a/R5b (#836/#837): fail before any other step after unsuccessful authz,
# and require authz success on every later step, including third-party uses.
AUTHZ_REFUSAL_STEP = """      - name: Refuse an unauthorised run before anything else
        if: ${{ needs.authz.result != 'success' }}
        run: |
          echo "::error::authz did not pass; nothing of this run's code is checked out or run"
          exit 1
"""
# Independently pin the decoded YAML literal block, including its clipped newline.
AUTHZ_REFUSAL_RUN = (
    "echo \"::error::authz did not pass; nothing of this run's code is checked out or run\"\n"
    "exit 1\n"
)
AUTHZ_REFUSAL_JOBS = [("gate.yml", "gate")]
# A skipped required check counts as success; cancellation must not skip gate.
GATE_CONDITION = "${{ always() }}"
DOCS_CONDITION = "needs.preflight-docs.outputs.docs_only != 'true'"
CI_CONDITIONS = {
    "gate.yml": {
        "authz": None,
        "preflight-docs": None,
        "differential": DOCS_CONDITION,
        "pytest": DOCS_CONDITION,
        "gate": GATE_CONDITION,
    },
    "test.yml": {
        "authz": None,
        "preflight-docs": None,
        "pytest": DOCS_CONDITION,
    },
}
CI_NEEDS = {
    "gate.yml": {
        "authz": [],
        "preflight-docs": ["authz"],
        "differential": ["authz", "preflight-docs"],
        "pytest": ["authz", "preflight-docs"],
        "gate": ["authz", "preflight-docs", "differential", "pytest"],
    },
    "test.yml": {
        "authz": [],
        "preflight-docs": ["authz"],
        "pytest": ["authz", "preflight-docs"],
    },
}
GATE_ENV = {
    "AUTHZ_RESULT": "${{ needs.authz.result }}",
    "PREFLIGHT_RESULT": "${{ needs.preflight-docs.result }}",
    "DOCS_ONLY": "${{ needs.preflight-docs.outputs.docs_only }}",
    "DIFFERENTIAL_RESULT": "${{ needs.differential.result }}",
    "PYTEST_RESULT": "${{ needs.pytest.result }}",
}
JOB_RESULTS = ("success", "skipped", "failure", "cancelled")
AUTHZ_TEST_REPO = "pineforge-4pass/pineforge-codegen-oss"
CI_JOBS = [
    (filename, job_id)
    for filename, jobs in CI_CONDITIONS.items()
    for job_id in jobs
]
# These workflows hold secrets or OIDC permissions: pending pf-ci-release.
# There is no claim-name job in test.yml at aebad8c; it is in release.yml.
CREDENTIAL_RUNNERS = {
    "release.yml": {"release": "ubuntu-latest", "claim-name": "ubuntu-24.04"},
    "publish-pyodide.yml": {"publish": "ubuntu-latest", "claim-npm": "ubuntu-24.04"},
    "promote-baseline.yml": {"promote": "ubuntu-latest"},
}
CREDENTIAL_JOBS = [
    (filename, job_id)
    for filename, jobs in CREDENTIAL_RUNNERS.items()
    for job_id in jobs
]


def job_blocks(text):
    """Read the top-level jobs in the repository's block-style workflows."""
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(
        r"""^  ([\w-]+|'[^']+'|"[^"]+"):[ \t]*(?:#.*)?$""", body, re.M
    ))
    assert matches, "workflow jobs must use the checked block format"
    return {
        match[1].strip("'\""): body[
            match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)
        ]
        for index, match in enumerate(matches)
    }


def workflow_trigger(text):
    trigger = re.search(r"""^(?:on|'on'|"on"):[^\n]*(?:\n(?:[ \t]+[^\n]*|))*""", text, re.M)
    assert trigger is not None, "workflow must declare its trigger"
    return trigger[0]


def job_values(body, key):
    return re.findall(
        rf"^    {re.escape(key)}:[ \t]*([^\n]*(?:\n[ \t]{{6,}}[^\n]*)*)",
        body, re.M,
    )


def ci_runner(job_id):
    return AUTHZ_RUNNER if job_id == "authz" else CI_RUNNER


def job_needs(body):
    values = job_values(body, "needs")
    assert len(values) <= 1, "a job must declare needs only once"
    if not values:
        return []
    value = values[0].strip()
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    if value.startswith("- "):
        return re.findall(r"^[ \t]*- ['\"]?([\w-]+)['\"]?[ \t]*$", value, re.M)
    return [value.strip("'\"")]


def job_steps(body):
    steps = re.search(
        r"^    steps:[ \t]*\n((?:[ ]{6,}[^\n]*(?:\n|$)|\n)*)", body, re.M
    )
    assert steps is not None, "the checked job must declare steps"
    matches = list(re.finditer(r"^      - ", steps[1], re.M))
    assert matches and matches[0].start() == 0, "steps must start with a checked step"
    return [
        steps[1][
            match.start():matches[index + 1].start() if index + 1 < len(matches) else len(steps[1])
        ].rstrip("\n") + "\n"
        for index, match in enumerate(matches)
    ]


def named_step(body, name):
    matches = [
        step for step in job_steps(body)
        if step.startswith(f"      - name: {name}\n")
    ]
    assert len(matches) == 1, f"the checked job must have one {name!r} step"
    return matches[0]


def step_values(step, key):
    """Read step fields, including a field written immediately after the dash."""
    first, rest = step.split("\n", 1)
    normalized = "        " + first[8:] + "\n" + rest
    return re.findall(
        rf"^        {re.escape(key)}:[ \t]*([^\n]*(?:\n[ \t]{{10,}}[^\n]*)*)",
        normalized, re.M,
    )


def step_requires_authz_success(step):
    conditions = step_values(step, "if")
    if len(conditions) != 1:
        return False
    condition = conditions[0].strip()
    if condition.startswith("${{") and condition.endswith("}}"):
        condition = condition[3:-2].strip()
    if condition != AUTHZ_SUCCESS and not condition.startswith(AUTHZ_SUCCESS + " && "):
        return False
    # A leading success comparison must not be bypassed by a top-level OR.
    depth = 0
    for token in re.findall(r"'(?:[^']|'')*'|[()]|\|\|", condition):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth < 0:
                return False
        elif token == "||" and depth == 0:
            return False
    return depth == 0


def assert_no_job_fork_conditions(text):
    for job_id, body in job_blocks(text).items():
        for condition in job_values(body, "if"):
            assert "head.repo.full_name" not in condition and "github.repository" not in condition, (
                f"{job_id}: job-level fork guards must not skip required checks"
            )


def assert_authz_job(text):
    jobs = job_blocks(text)
    assert next(iter(jobs)) == "authz", "the canonical authz job must be first"
    actual = "  authz:" + jobs["authz"].rstrip("\n") + "\n"
    assert actual == AUTHZ_JOB, "authz must equal the canonical ci-labels.md job verbatim"


def assert_authz_dependencies(text):
    for job_id, body in job_blocks(text).items():
        if job_id == "authz":
            continue
        assert "authz" in job_needs(body), f"{job_id}: every non-authz job needs authz"


def assert_authz_refusal_steps(text):
    for job_id, body in job_blocks(text).items():
        if "authz" not in job_needs(body):
            continue
        if not any(
            re.search(r"\b(?:always|cancelled|failure|success)\s*\(", condition)
            for condition in job_values(body, "if")
        ):
            continue
        assert not job_values(body, "continue-on-error"), (
            f"{job_id}: an authz refusal must not ignore the job failure"
        )
        steps = job_steps(body)
        assert steps[0] == AUTHZ_REFUSAL_STEP, (
            f"{job_id}: the exact authz refusal must be the first step before any run or uses"
        )
        assert job_shell(steps[0]) == AUTHZ_REFUSAL_RUN, (
            f"{job_id}: authz refusal run must equal the canon two-line text with one final newline"
        )
        for index, step in enumerate(steps[1:], start=2):
            assert step_requires_authz_success(step), (
                f"{job_id} step {index}: every later step must require an authz success condition"
            )


def assert_unfiltered_pr_trigger(text):
    trigger = workflow_trigger(text)
    assert re.search(r"\bpull_request\b", trigger), "authz workflow must run on pull_request"
    event = re.search(
        r"""^  ['"]?pull_request['"]?:[^\n]*(?:\n[ \t]{4,}[^\n]*)*""", trigger, re.M
    )
    # Scalar/list triggers have no event filters. Inline mappings are checked too.
    event_text = event[0] if event else trigger
    assert not re.search(
        r"""\b(?:paths|paths-ignore|branches|branches-ignore)['"]?\s*:""", event_text
    ), "authz pull_request trigger must not have paths or branches filters"


def assert_gate_context(text, *, gate_workflow=False):
    trigger = workflow_trigger(text)
    if gate_workflow:
        assert "pull_request_target" not in trigger, "CI must not use pull_request_target"
        assert re.search(r"\bpull_request\b", trigger), "gate must run on pull_request"
        assert "gate" in job_blocks(text), "the required gate aggregate must remain"
    if not re.search(r"\bpull_request\b", trigger):
        return
    for job_id, body in job_blocks(text).items():
        name = re.search(r"^    name:[ \t]*([^\n]+)", body, re.M)
        if gate_workflow and job_id == "gate":
            assert job_values(body, "if") == [GATE_CONDITION], (
                "the trusted gate aggregate must use exactly ${{ always() }}"
            )
            if name:
                assert name[1].split("#", 1)[0].strip().strip("'\"") == "gate", (
                    "the required aggregate must keep its gate name"
                )
            continue
        assert job_id != "gate", (
            "only the authz-protected gate workflow may publish the trusted gate check"
        )
        if name:
            assert name[1].split("#", 1)[0].strip().strip("'\"") != "gate", (
                "only the authz-protected gate aggregate may publish the trusted gate check"
            )


def assert_pr_jobs(text):
    if not re.search(r"\bpull_request\b", workflow_trigger(text)):
        return
    assert_no_job_fork_conditions(text)
    assert_authz_job(text)
    assert_unfiltered_pr_trigger(text)
    assert_authz_dependencies(text)
    assert_authz_refusal_steps(text)
    for job_id, body in job_blocks(text).items():
        assert job_values(body, "runs-on") == [ci_runner(job_id)], (
            f"{job_id}: every PR-triggerable job must keep its literal CI runner"
        )


def test_only_the_authz_protected_gate_workflow_can_claim_the_gate_context():
    for workflow in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        text = workflow.read_text(encoding="utf-8")
        assert_gate_context(text, gate_workflow=workflow.name == "gate.yml")
        assert_pr_jobs(text)


@pytest.mark.parametrize("trigger", [
    "on:\n  pull_request:\n    branches: [main]",
    "on: [push, pull_request]",
    "on: pull_request",
    "'on':\n  'pull_request': {}",
])
@pytest.mark.parametrize("job", [
    "  gate:",
    "  'gate':",
    '  "gate":',
    "  tests:\n    name: gate",
    "  tests:\n    name: 'gate'",
    '  tests:\n    name: "gate" # reserved',
])
def test_gate_context_guard_rejects_reserved_ids_and_names(trigger, job):
    with pytest.raises(AssertionError, match="trusted gate check"):
        assert_gate_context(
            f"{trigger}\n\njobs:\n{job}\n"
            f"    if: always()\n    runs-on: {CI_RUNNER}\n"
            "    steps:\n      - run: pwd\n"
        )


def test_gate_context_guard_refuses_the_old_target_workflow():
    with pytest.raises(AssertionError, match="pull_request_target"):
        assert_gate_context(
            "on:\n  pull_request_target:\n\njobs:\n  gate:\n    runs-on: ubuntu-24.04\n",
            gate_workflow=True,
        )


@pytest.mark.parametrize("bad_job", [
    f"    runs-on: {CI_RUNNER}\n",
    f"    if: always()\n    runs-on: {CI_RUNNER}\n",
    f"    if: always() || ({FORBIDDEN_FORK_GUARD})\n    runs-on: {CI_RUNNER}\n",
    f"    if: ({FORBIDDEN_FORK_GUARD}) || true\n    runs-on: {CI_RUNNER}\n",
    "    needs: authz\n    runs-on: ubuntu-24.04\n",
])
def test_pr_authz_policy_also_refuses_violations_in_another_workflow(bad_job):
    with pytest.raises(AssertionError):
        assert_pr_jobs(
            f"on: [push, pull_request]\n\njobs:\n{AUTHZ_JOB}\n  other:\n{bad_job}"
            "    steps:\n      - run: pwd\n"
        )


def test_gate_context_guard_requires_the_pr_trigger_and_aggregate():
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    assert "\n  pull_request:\n" in text
    mutant = text.replace("\n  pull_request:\n", "\n  push:\n", 1)
    with pytest.raises(AssertionError, match="gate must run on pull_request"):
        assert_gate_context(mutant, gate_workflow=True)
    assert "\n  gate:\n" in text
    mutant = text.replace("\n  gate:\n", "\n  other:\n", 1)
    with pytest.raises(AssertionError, match="required gate aggregate"):
        assert_gate_context(mutant, gate_workflow=True)


@pytest.mark.parametrize("bad_name", ["gate", "'gate'", '"gate" # reserved'])
def test_gate_context_guard_refuses_another_job_named_gate_in_its_own_workflow(bad_name):
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    mutant = replace_job_text(
        text, "differential", "    needs: [authz, preflight-docs]\n",
        f"    name: {bad_name}\n    needs: [authz, preflight-docs]\n",
    )
    with pytest.raises(AssertionError, match="trusted gate check"):
        assert_gate_context(mutant, gate_workflow=True)


def test_gate_context_guard_refuses_renaming_the_required_aggregate():
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    original = f"    if: {GATE_CONDITION}\n"
    mutant = replace_job_text(text, "gate", original, "    name: other\n" + original)
    with pytest.raises(AssertionError, match="keep its gate name"):
        assert_gate_context(mutant, gate_workflow=True)


def assert_ci_workflow(text, filename):
    assert "pull_request_target" not in workflow_trigger(text), "CI must not use pull_request_target"
    assert "|| 'ubuntu-24.04'" not in text, "CI must not have a hosted fallback"
    assert not re.search(
        r"""github\.(?:actor|triggering_actor)\s*!=\s*['"]dependabot\[bot\]['"]""", text
    ), "Dependabot must use the same pool as other same-repository PRs"
    assert "PF_CI_LABELS" not in text, "literal arrays need no PF_CI_LABELS env"
    jobs = job_blocks(text)
    assert set(jobs) == set(CI_CONDITIONS[filename]), "CI job inventory must stay explicit"
    assert_no_job_fork_conditions(text)
    assert_authz_dependencies(text)
    assert_authz_refusal_steps(text)
    header = text.split("\njobs:\n", 1)[0]
    for job_id, body in jobs.items():
        assert job_values(body, "runs-on") == [ci_runner(job_id)], (
            f"{job_id} must keep its literal pf-ci-x64 runner class"
        )
        assert all("pull_request_target" not in value for value in job_values(body, "if")), (
            "CI must not use pull_request_target"
        )
        condition = CI_CONDITIONS[filename][job_id]
        assert job_values(body, "if") == ([] if condition is None else [condition]), (
            f"{job_id} must keep existing job conditions without fork guards"
        )
        assert job_needs(body) == CI_NEEDS[filename][job_id], (
            f"{job_id} must retain authz and all existing needs"
        )
        assert not re.search(
            r"\bid-token:[ \t]*write\b|\bsecrets[.\[]|^    environment:",
            header + body, re.M,
        ), f"{job_id}: credential jobs must not use the CI pool"
        if job_id != "authz":
            assert "pull_request_target" not in body, "CI must not use pull_request_target"
    assert_authz_job(text)
    assert_unfiltered_pr_trigger(text)
    assert_gate_context(text, gate_workflow=filename == "gate.yml")
    if filename == "gate.yml":
        assert_gate_result_bindings(text)


def replace_job_text(text, job_id, old, new):
    body = job_blocks(text)[job_id]
    assert text.count(body) == 1, "mutation must select exactly one job"
    assert body.count(old) == 1, "mutation must replace exactly one existing setting"
    mutant = body.replace(old, new, 1)
    assert mutant != body, "mutation must change the checked workflow"
    return text.replace(body, mutant, 1)


def replace_step_text(text, job_id, name, old, new):
    step = named_step(job_blocks(text)[job_id], name)
    assert step.count(old) == 1, "mutation must replace exactly one existing step setting"
    replacement = step.replace(old, new, 1)
    assert replacement != step, "mutation must change the checked step"
    return replace_job_text(text, job_id, step, replacement)


def replace_job_condition(text, job_id, condition):
    values = job_values(job_blocks(text)[job_id], "if")
    assert len(values) <= 1
    replacement = "" if condition is None else f"    if: {condition}\n"
    if values:
        return replace_job_text(text, job_id, f"    if: {values[0]}\n", replacement)
    original = f"    runs-on: {ci_runner(job_id)}\n"
    return replace_job_text(text, job_id, original, replacement + original)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
def test_ci_workflows_use_literal_pool_runners_and_canonical_authz(filename):
    assert_ci_workflow((WORKFLOWS / filename).read_text(encoding="utf-8"), filename)


@pytest.mark.parametrize("filename,job_id", CI_JOBS)
@pytest.mark.parametrize("bad_runner", [
    "ubuntu-24.04",
    """${{ fromJSON('["self-hosted","pf-ci","pf-ci-x64"]') }}""",
    """${{ fromJSON('["self-hosted","pf-ci","pf-ci-x64"]') || 'ubuntu-24.04' }}""",
    '["self-hosted","pf-ci","pf-ci-x64-16"]',
    '["self-hosted","pf-ci","pf-ci-crj"]',
    '["self-hosted","pf-ci","pf-ci-release"]',
    '["self-hosted","pf-ci"]',
])
def test_ci_policy_refuses_fallbacks_expressions_and_runner_class_changes(
    filename, job_id, bad_runner
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = replace_job_text(
        text, job_id, f"    runs-on: {ci_runner(job_id)}\n", f"    runs-on: {bad_runner}\n"
    )
    with pytest.raises(AssertionError):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename,job_id", CI_JOBS)
@pytest.mark.parametrize("bad_condition", [
    FORBIDDEN_FORK_GUARD,
    f"({DOCS_CONDITION}) && ({FORBIDDEN_FORK_GUARD})",
    f"always() && ({FORBIDDEN_FORK_GUARD})",
    f"({FORBIDDEN_FORK_GUARD}) || true",
    ">-\n      github.event_name != 'pull_request' ||\n"
    "      github.event.pull_request.head.repo.full_name == github.repository",
])
def test_ci_policy_refuses_reintroducing_any_job_fork_guard(filename, job_id, bad_condition):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = replace_job_condition(text, job_id, bad_condition)
    with pytest.raises(AssertionError, match="job-level fork guards"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename,job_id", [
    (filename, job_id) for filename, job_id in CI_JOBS if job_id != "authz"
])
def test_ci_policy_refuses_removing_each_authz_dependency(filename, job_id):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    values = job_values(job_blocks(text)[job_id], "needs")
    assert len(values) == 1
    remaining = [need for need in CI_NEEDS[filename][job_id] if need != "authz"]
    replacement = f"    needs: [{', '.join(remaining)}]\n" if remaining else ""
    mutant = replace_job_text(
        text, job_id, f"    needs: {values[0]}\n", replacement
    )
    with pytest.raises(AssertionError, match="every non-authz job needs authz"):
        assert_ci_workflow(mutant, filename)


def mutate_authz_job(text, mutation):
    """Mutate a unique setting in a real workflow, never the expected literal."""
    if mutation == "drop-fork-check":
        original = (
            '              [ "$HEAD_IS_FORK" = "false" ] || fail "pull request from a fork"\n'
        )
        replacement = ""
    elif mutation == "drop-head-repo-check":
        original = (
            '              [ "$HEAD_REPO" = "$REPO" ] || fail '
            '"pull request from another repository (${HEAD_REPO:-unknown})"\n'
        )
        replacement = ""
    elif mutation == "fail-no-op":
        original = (
            '          fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" '
            '>> "$GITHUB_STEP_SUMMARY"; exit 1; }\n'
        )
        replacement = "          fail() { :; }\n"
    elif mutation == "drop-default-arm":
        original = (
            '            *) fail "event $EVENT does not run CI '
            '(pull_request_target and other events are not allowed)" ;;\n'
        )
        replacement = ""
    elif mutation == "drop-workflow-run-arm":
        body = job_blocks(text)["authz"]
        start = body.index("            workflow_run)\n")
        end = body.index("            pull_request)\n", start)
        original, replacement = body[start:end], ""
    elif mutation == "drop-workflow-run-head-check":
        original = (
            '              [ "$WR_HEAD_REPO" = "$REPO" ] || fail '
            '"workflow_run triggered from another repository (${WR_HEAD_REPO:-unknown})"\n'
        )
        replacement = ""
    elif mutation == "drop-workflow-run-lookup":
        original = (
            '                  perm=$(gh api "repos/$REPO/collaborators/$WR_ACTOR/permission" '
            '--jq .permission 2>/dev/null || echo none)\n'
        )
        replacement = "                  perm=write\n"
    elif mutation == "lookup-fails-open":
        original = (
            '                  perm=$(gh api "repos/$REPO/collaborators/$WR_ACTOR/permission" '
            '--jq .permission 2>/dev/null || echo none)\n'
        )
        replacement = original.replace("|| echo none)", "|| echo write)")
    elif mutation == "allow-read-permission":
        original = """                  case "$perm" in admin|maintain|write) ;; *) fail "workflow_run triggering actor ${WR_ACTOR:-unknown} has permission '$perm', not write" ;; esac ;;
"""
        replacement = original.replace("admin|maintain|write)", "admin|maintain|write|read)")
    elif mutation in {"drop-pr-lookup", "pr-lookup-fails-open"}:
        original = (
            '                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" '
            '--jq .permission 2>/dev/null || echo none)\n'
        )
        replacement = (
            "" if mutation == "drop-pr-lookup"
            else original.replace("|| echo none)", "|| echo write)")
        )
    elif mutation == "pr-allow-read-permission":
        original = """                      case "$perm" in admin|maintain|write) ;; *) fail "author ${PR_AUTHOR:-unknown} (${ASSOCIATION:-NONE}) has permission '$perm', not write, and is not an allowlisted bot" ;; esac ;;
"""
        replacement = original.replace("admin|maintain|write)", "admin|maintain|write|read)")
    elif mutation == "stale-authz-job":
        # Restore the previous canon's entire PR fallback, including its old
        # refusal wording. The resulting real workflow must now be refused.
        original = """                    *)
                      # A member whose org membership is private shows as CONTRIBUTOR: ask GitHub for the
                      # author's permission on this repository; anything but admin/maintain/write (or a
                      # failed call) refuses.
                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo none)
                      case "$perm" in admin|maintain|write) ;; *) fail "author ${PR_AUTHOR:-unknown} (${ASSOCIATION:-NONE}) has permission '$perm', not write, and is not an allowlisted bot" ;; esac ;;
"""
        replacement = """                    *) fail "author ${PR_AUTHOR:-unknown} (${ASSOCIATION:-NONE}) is not an owner, member, collaborator or allowlisted bot" ;;
"""
    elif mutation == "allow-target-event":
        original = (
            "            push|workflow_dispatch|schedule|merge_group|release|"
            "workflow_call|repository_dispatch)\n"
        )
        replacement = original.replace(")\n", "|pull_request_target)\n")
    elif mutation == "drop-permissions":
        original, replacement = "    permissions: {}\n", ""
    elif mutation == "contents-write":
        original = "    permissions: {}\n"
        replacement = "    permissions:\n      contents: write\n"
    elif mutation == "change-allowed-bots":
        original = '          ALLOWED_BOTS: "dependabot[bot] pineforge-app[bot]"\n'
        replacement = '          ALLOWED_BOTS: "dependabot[bot] pineforge-app[bot] other[bot]"\n'
    elif mutation == "allow-unaffiliated-author":
        original = "                OWNER|MEMBER|COLLABORATOR) ;;\n"
        replacement = "                OWNER|MEMBER|COLLABORATOR|NONE) ;;\n"
    elif mutation == "drop-name":
        original, replacement = "    name: authz\n", ""
    elif mutation == "drop-strict-shell":
        original, replacement = "          set -eu\n", ""
    elif mutation == "add-if":
        original = f"    runs-on: {AUTHZ_RUNNER}\n"
        replacement = "    if: true\n" + original
    elif mutation in {"dependabot-exemption", "actor-check"}:
        original = '          case "$EVENT" in\n'
        if mutation == "dependabot-exemption":
            replacement = '          [ "$PR_AUTHOR" != "dependabot[bot]" ] || exit 0\n' + original
        else:
            replacement = '          [ "$PR_AUTHOR" = maintainer ] || exit 1\n' + original
    elif mutation == "checkout":
        original = "    steps:\n"
        replacement = original + "      - uses: actions/checkout@v4\n"
    elif mutation == "continue-on-error":
        original = "    steps:\n"
        replacement = "    continue-on-error: true\n" + original
    elif mutation == "step-if":
        original = "      - name: Only our own pull requests and our own events run CI\n"
        replacement = original + "        if: false\n"
    elif mutation == "extra-step":
        original = (
            '          echo "authz: allowed ($EVENT${PR_AUTHOR:+ by $PR_AUTHOR})" '
            '>> "$GITHUB_STEP_SUMMARY"\n'
        )
        replacement = original + "      - run: true\n"
    else:
        raise AssertionError(f"unknown authz mutation: {mutation}")
    return replace_job_text(text, "authz", original, replacement)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("mutation", [
    "fail-no-op", "drop-fork-check", "drop-head-repo-check", "drop-default-arm",
    "drop-workflow-run-arm", "drop-workflow-run-head-check", "drop-workflow-run-lookup",
    "lookup-fails-open", "allow-read-permission", "allow-target-event",
    "drop-pr-lookup", "pr-lookup-fails-open", "pr-allow-read-permission", "stale-authz-job",
    "drop-permissions", "contents-write", "change-allowed-bots",
    "allow-unaffiliated-author", "drop-name", "drop-strict-shell",
    "add-if", "dependabot-exemption", "actor-check", "checkout",
    "continue-on-error", "extra-step", "step-if",
])
def test_authz_policy_refuses_changes_to_the_verbatim_job(filename, mutation):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = mutate_authz_job(text, mutation)
    with pytest.raises(AssertionError, match="canonical ci-labels.md job verbatim"):
        assert_authz_job(mutant)
    with pytest.raises(AssertionError):
        assert_ci_workflow(mutant, filename)
    with pytest.raises(AssertionError, match="canonical ci-labels.md job verbatim"):
        assert_pr_jobs(mutant)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("env_name", [
    "EVENT", "REPO", "HEAD_REPO", "HEAD_IS_FORK", "ASSOCIATION", "PR_AUTHOR",
    "WR_HEAD_REPO", "WR_ACTOR", "GH_TOKEN", "ALLOWED_BOTS",
])
def test_authz_policy_refuses_removing_any_canonical_env_binding(filename, env_name):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    lines = re.findall(
        rf"^          {re.escape(env_name)}:[^\n]*\n", job_blocks(text)["authz"], re.M
    )
    assert len(lines) == 1, "the mutation must select one real canonical env binding"
    mutant = replace_job_text(text, "authz", lines[0], "")
    with pytest.raises(AssertionError, match="canonical ci-labels.md job verbatim"):
        assert_authz_job(mutant)
    with pytest.raises(AssertionError, match="canonical ci-labels.md job verbatim"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
def test_authz_policy_refuses_a_missing_or_later_authz_job(filename):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert text.count(AUTHZ_JOB + "\n") == 1
    without_authz = text.replace(AUTHZ_JOB + "\n", "", 1)
    for mutant in (without_authz, without_authz + "\n" + AUTHZ_JOB):
        with pytest.raises(AssertionError, match="canonical authz job must be first"):
            assert_authz_job(mutant)
        with pytest.raises(AssertionError):
            assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
def test_status_function_jobs_require_the_exact_authz_refusal_first(filename):
    assert_authz_refusal_steps((WORKFLOWS / filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename,job_id", AUTHZ_REFUSAL_JOBS)
def test_authz_refusal_is_the_first_step_in_each_affected_job(filename, job_id):
    body = job_blocks((WORKFLOWS / filename).read_text(encoding="utf-8"))[job_id]
    assert "authz" in job_needs(body)
    assert job_values(body, "if") == [GATE_CONDITION]
    assert job_steps(body)[0] == AUTHZ_REFUSAL_STEP
    assert job_shell(job_steps(body)[0]) == AUTHZ_REFUSAL_RUN
    for step in job_steps(body)[1:]:
        assert step_requires_authz_success(step)


@pytest.mark.parametrize("filename,job_id", AUTHZ_REFUSAL_JOBS)
@pytest.mark.parametrize("mutation", [
    "drop", "after-checkout", "after-local-action", "after-repo-script",
    "after-setup-action", "after-result-step", "invert-test", "accept-skipped",
    "ignore-exit", "forge-condition", "rename", "step-if", "step-continue-on-error",
    "trailing-continue-on-error", "job-continue-on-error", "legacy-shell",
    "one-line-shell", "drop-exit", "echo-only", "strip-final-newline",
])
def test_authz_refusal_policy_refuses_missing_moved_or_weakened_first_steps(
    filename, job_id, mutation
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    if mutation == "drop":
        mutant = replace_job_text(text, job_id, AUTHZ_REFUSAL_STEP, "")
    elif mutation.startswith("after-"):
        preceding = {
            "after-checkout": "      - uses: actions/checkout@v4\n",
            "after-local-action": "      - uses: ./.github/actions/preflight\n",
            "after-repo-script": "      - run: python scripts/preflight.py\n",
            "after-setup-action": "      - uses: actions/setup-python@v6\n",
            "after-result-step": named_step(job_blocks(text)[job_id], "Check gate results"),
        }[mutation]
        if mutation == "after-result-step":
            mutant = replace_job_text(text, job_id, AUTHZ_REFUSAL_STEP + preceding,
                                      preceding + AUTHZ_REFUSAL_STEP)
        else:
            mutant = replace_job_text(text, job_id, AUTHZ_REFUSAL_STEP,
                                      preceding + AUTHZ_REFUSAL_STEP)
    elif mutation == "job-continue-on-error":
        mutant = replace_job_text(
            text, job_id, "    steps:\n", "    continue-on-error: true\n    steps:\n"
        )
    elif mutation == "legacy-shell":
        legacy = """      - name: Refuse unless authz succeeded
        env:
          AUTHZ_RESULT: ${{ needs.authz.result }}
        run: test "$AUTHZ_RESULT" = success || { echo "::error::authz did not succeed"; exit 1; }
"""
        mutant = replace_job_text(text, job_id, AUTHZ_REFUSAL_STEP, legacy)
    else:
        if mutation == "invert-test":
            original, replacement = "!= 'success'", "== 'success'"
        elif mutation == "accept-skipped":
            original, replacement = "!= 'success'", "!= 'skipped'"
        elif mutation == "ignore-exit":
            original, replacement = "exit 1", "exit 0"
        elif mutation == "one-line-shell":
            original = AUTHZ_REFUSAL_STEP
            replacement = original.split("        run: |\n", 1)[0] + (
                f"        run: {'; '.join(AUTHZ_REFUSAL_RUN.splitlines())}\n"
            )
        elif mutation == "drop-exit":
            original, replacement = "          exit 1\n", ""
        elif mutation == "echo-only":
            original, replacement = "exit 1", 'echo "exit 1"'
        elif mutation == "strip-final-newline":
            original, replacement = "        run: |\n", "        run: |-\n"
        elif mutation == "forge-condition":
            original, replacement = "needs.authz.result", "'success'"
        elif mutation == "rename":
            original = "Refuse an unauthorised run before anything else"
            replacement = "Check authz later"
        elif mutation == "trailing-continue-on-error":
            original = AUTHZ_REFUSAL_STEP
            replacement = original + "        continue-on-error: true\n"
        elif mutation == "step-if":
            original = "        if: ${{ needs.authz.result != 'success' }}\n"
            replacement = "        if: false\n"
        else:
            original = "        if: ${{ needs.authz.result != 'success' }}\n"
            replacement = "        continue-on-error: true\n" + original
        mutant = replace_step_text(
            text, job_id, "Refuse an unauthorised run before anything else", original, replacement
        )
    with pytest.raises(AssertionError, match="authz refusal"):
        assert_authz_refusal_steps(mutant)
    with pytest.raises(AssertionError, match="authz refusal"):
        assert_ci_workflow(mutant, filename)
    with pytest.raises(AssertionError, match="authz refusal"):
        assert_pr_jobs(mutant)


@pytest.mark.parametrize("filename,job_id", AUTHZ_REFUSAL_JOBS)
def test_authz_success_policy_refuses_an_unguarded_gate_result_step(filename, job_id):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = replace_step_text(
        text, job_id, "Check gate results", f"        if: {AUTHZ_SUCCESS}\n", ""
    )
    with pytest.raises(AssertionError, match="authz success condition"):
        assert_authz_refusal_steps(mutant)
    with pytest.raises(AssertionError, match="authz success condition"):
        assert_ci_workflow(mutant, filename)
    with pytest.raises(AssertionError, match="authz success condition"):
        assert_pr_jobs(mutant)


@pytest.mark.parametrize("step_setting", [
    "uses: actions/checkout@v4\n",
    "uses: ./.github/actions/preflight\n",
    "run: python scripts/preflight.py\n",
    "run: |\n          python scripts/preflight.py\n",
    "uses: actions/setup-python@v6\n",
    "uses: actions/upload-artifact@v7\n",
])
@pytest.mark.parametrize("old_condition", [
    None, "always()", "failure()", "steps.docs.outputs.docs_only == 'true'",
    "failure() || cancelled()",
])
@pytest.mark.parametrize("mutation", [
    "drop", "invert", "accept-skipped", "or-bypass", "old-condition-only",
])
def test_authz_success_policy_preserves_conditions_and_refuses_unsafe_later_steps(
    step_setting, old_condition, mutation
):
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    condition = AUTHZ_SUCCESS
    if old_condition is not None:
        condition += f" && ({old_condition})"
    following = (
        "      - name: Protected follow-up\n"
        f"        if: {condition}\n"
        f"        {step_setting}"
    )
    result_step = named_step(job_blocks(text)["gate"], "Check gate results")
    guarded = replace_job_text(text, "gate", result_step, result_step + following)
    assert_authz_refusal_steps(guarded)
    assert_ci_workflow(guarded, "gate.yml")
    assert_pr_jobs(guarded)
    if mutation == "drop":
        replacement = ""
    elif mutation == "invert":
        replacement = f"        if: {condition.replace('==', '!=', 1)}\n"
    elif mutation == "accept-skipped":
        replacement = f"        if: {condition.replace('success', 'skipped', 1)}\n"
    elif mutation == "or-bypass":
        replacement = f"        if: {condition} || true\n"
    else:
        replacement = f"        if: {old_condition or 'always()'}\n"
    mutant = replace_step_text(
        guarded, "gate", "Protected follow-up", f"        if: {condition}\n", replacement
    )
    with pytest.raises(AssertionError, match="authz success condition"):
        assert_authz_refusal_steps(mutant)
    with pytest.raises(AssertionError, match="authz success condition"):
        assert_ci_workflow(mutant, "gate.yml")
    with pytest.raises(AssertionError, match="authz success condition"):
        assert_pr_jobs(mutant)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("status_condition", [
    "always()", "!cancelled()", "cancelled()", "failure()", "!success()",
    "(success() || failure())",
])
@pytest.mark.parametrize("authz_success_condition", [False, True])
@pytest.mark.parametrize("condition_style", ["plain", "expression", "folded"])
def test_status_function_conditions_cannot_bypass_the_first_authz_refusal(
    filename, status_condition, authz_success_condition, condition_style
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    job_id = "differential" if filename == "gate.yml" else "pytest"
    condition = f"({status_condition}) && {DOCS_CONDITION}"
    if authz_success_condition:
        condition += f" && {AUTHZ_SUCCESS}"
    if condition_style == "expression":
        condition = "${{ " + condition + " }}"
    elif condition_style == "folded":
        condition = ">-\n      " + condition
    guarded = replace_job_condition(text, job_id, condition)
    # Build a valid status-function version of the existing job, keeping
    # every old step condition as a conjunct of the new authz requirement.
    for step in job_steps(job_blocks(guarded)[job_id]):
        conditions = step_values(step, "if")
        if conditions:
            assert len(conditions) == 1
            replacement = step.replace(
                f"        if: {conditions[0]}\n",
                f"        if: {AUTHZ_SUCCESS} && ({conditions[0]})\n",
                1,
            )
        else:
            first, rest = step.split("\n", 1)
            replacement = f"{first}\n        if: {AUTHZ_SUCCESS}\n{rest}"
        guarded = replace_job_text(guarded, job_id, step, replacement)
    guarded = replace_job_text(
        guarded, job_id, "    steps:\n", "    steps:\n" + AUTHZ_REFUSAL_STEP
    )
    assert_authz_refusal_steps(guarded)
    mutant = replace_job_text(guarded, job_id, AUTHZ_REFUSAL_STEP, "")
    with pytest.raises(AssertionError, match="authz refusal"):
        assert_authz_refusal_steps(mutant)
    with pytest.raises(AssertionError, match="authz refusal"):
        assert_pr_jobs(mutant)
    steps = job_steps(job_blocks(guarded)[job_id])[1:]
    checkout = next(
        step for step in steps
        if any(value.startswith("actions/checkout@") for value in step_values(step, "uses"))
    )
    run_step = next(step for step in steps if step_values(step, "run"))
    for step in (checkout, run_step):
        step_condition = step_values(step, "if")[0]
        mutant = replace_job_text(
            guarded, job_id, step, step.replace(f"        if: {step_condition}\n", "", 1)
        )
        with pytest.raises(AssertionError, match="authz success condition"):
            assert_authz_refusal_steps(mutant)
        with pytest.raises(AssertionError, match="authz success condition"):
            assert_pr_jobs(mutant)


@pytest.mark.parametrize("status_condition", [
    "always()", "!cancelled()", "cancelled()", "failure()", "!success()",
    "(success() || failure())",
])
def test_authz_refusal_policy_covers_new_jobs_in_another_workflow(status_condition):
    text = (
        f"on: pull_request\n\njobs:\n{AUTHZ_JOB}\n  failure-report:\n"
        f"    needs: authz\n    if: ({status_condition})\n    runs-on: {CI_RUNNER}\n"
        "    steps:\n" + AUTHZ_REFUSAL_STEP
        + "      - uses: actions/checkout@v4\n"
        + f"        if: {AUTHZ_SUCCESS}\n"
        + "      - run: python scripts/preflight.py\n"
        + f"        if: {AUTHZ_SUCCESS}\n"
    )
    assert_pr_jobs(text)
    mutant = replace_job_text(text, "failure-report", AUTHZ_REFUSAL_STEP, "")
    with pytest.raises(AssertionError, match="authz refusal"):
        assert_pr_jobs(mutant)


def assert_authz_refusal_shell(text, job_id, authz_result):
    step = job_steps(job_blocks(text)[job_id])[0]
    conditions = step_values(step, "if")
    assert len(conditions) == 1, "the refusal must have one result comparison"
    condition = conditions[0].strip()
    if condition.startswith("${{") and condition.endswith("}}"):
        condition = condition[3:-2].strip()
    comparison = re.fullmatch(
        r"needs\.authz\.result (==|!=) '([a-z]+)'", condition
    )
    assert comparison is not None, "the refusal must compare the actual authz result"
    command = job_shell(step)
    operator = "=" if comparison[1] == "==" else "!="
    # Translate the actual YAML comparison into shell so an inverted if or
    # weakened exit changes the observed result. Success skips the exit step.
    script = (
        f'if [ "$AUTHZ_RESULT" {operator} "{comparison[2]}" ]; then\n'
        f"{command}fi\n"
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={**os.environ, "AUTHZ_RESULT": authz_result},
        capture_output=True, text=True,
    )
    expected = 0 if authz_result == "success" else 1
    assert result.returncode == expected, (
        "authz refusal shell accepted a non-success result or rejected success: "
        + result.stdout + result.stderr
    )
    expected_output = "" if authz_result == "success" else (
        "::error::authz did not pass; nothing of this run's code is checked out or run\n"
    )
    assert result.stdout == expected_output
    assert result.stderr == ""


@pytest.mark.parametrize("filename,job_id", AUTHZ_REFUSAL_JOBS)
@pytest.mark.parametrize("authz_result", JOB_RESULTS)
def test_authz_refusal_shell_only_accepts_success(filename, job_id, authz_result):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_authz_refusal_shell(text, job_id, authz_result)


@pytest.mark.parametrize("filename,job_id", AUTHZ_REFUSAL_JOBS)
@pytest.mark.parametrize("original,replacement,authz_result", [
    ("!= 'success'", "== 'success'", "success"),
    ("!= 'success'", "== 'success'", "failure"),
    ("!= 'success'", "!= 'skipped'", "skipped"),
    ("exit 1", "exit 0", "cancelled"),
    ("          exit 1\n", "", "failure"),
    ("exit 1", 'echo "exit 1"', "cancelled"),
])
def test_authz_refusal_shell_refuses_inverted_or_weakened_step_mutations(
    filename, job_id, original, replacement, authz_result
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = replace_step_text(
        text, job_id, "Refuse an unauthorised run before anything else", original, replacement
    )
    with pytest.raises(AssertionError, match="authz refusal shell accepted"):
        assert_authz_refusal_shell(mutant, job_id, authz_result)


@pytest.mark.parametrize("filename,job_id,removed", [
    (filename, job_id, need)
    for filename, jobs in CI_NEEDS.items()
    for job_id, needs in jobs.items()
    for need in needs if need != "authz"
])
def test_ci_policy_refuses_removing_existing_needs(filename, job_id, removed):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    original = job_values(job_blocks(text)[job_id], "needs")[0]
    remaining = [need for need in CI_NEEDS[filename][job_id] if need != removed]
    mutant = replace_job_text(
        text, job_id, f"    needs: {original}\n",
        f"    needs: [{', '.join(remaining)}]\n",
    )
    with pytest.raises(AssertionError, match="all existing needs"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("filter_key", ["paths", "paths-ignore", "branches", "branches-ignore"])
def test_authz_policy_refuses_pr_trigger_filters(filename, filter_key):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    event = re.search(r"^  pull_request:[^\n]*\n", text, re.M)
    assert event is not None
    mutant = text.replace(event[0], f"  pull_request:\n    {filter_key}: ['docs/**']\n", 1)
    assert mutant != text
    with pytest.raises(AssertionError, match="must not have paths or branches filters"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
def test_authz_policy_requires_a_pull_request_trigger(filename):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    event = re.search(r"^  pull_request:[^\n]*\n(?:[ ]{4,}[^\n]*\n)*", text, re.M)
    assert event is not None
    mutant = text.replace(event[0], "", 1)
    assert mutant != text
    with pytest.raises(AssertionError, match="authz workflow must run on pull_request"):
        assert_ci_workflow(mutant, filename)


def job_shell(body):
    scripts = re.findall(r"^        run: \|\n((?:          [^\n]*\n|\n)+)", body, re.M)
    assert len(scripts) == 1, "the checked body must have exactly one block shell step"
    return "".join(line[10:] if line.startswith("          ") else line
                   for line in scripts[0].splitlines(keepends=True))


def assert_authz_shell(
    text, tmp_path, *, event="pull_request", head_repo=AUTHZ_TEST_REPO,
    head_is_fork="false", association="MEMBER", pr_author="member",
    wr_head_repo="", wr_actor="", permission="none", lookup_status=0,
    expected, lookup_expected=False,
):
    """Exercise the workflow's actual script; stub gh so tests never use the network."""
    summary = tmp_path / "authz-summary"
    lookup_log = tmp_path / "authz-gh-log"
    summary.write_text("", encoding="utf-8")
    lookup_log.write_text("", encoding="utf-8")
    allowed_bots = re.findall(
        r'^          ALLOWED_BOTS: "([^"\n]*)"\n', job_blocks(text)["authz"], re.M
    )
    assert len(allowed_bots) == 1, "the shell harness must use the workflow's bot allowlist"
    # The stub only supplies an API response. The workflow decides whether to
    # call it and which permissions to accept, including a failed lookup.
    stub = """gh() {
  printf '%s\\n' "$*" >> "$AUTHZ_GH_LOG"
  local authz_lookup_user
  case "$EVENT" in
    pull_request) authz_lookup_user=$PR_AUTHOR ;;
    workflow_run) authz_lookup_user=$WR_ACTOR ;;
    *) return 97 ;;
  esac
  [ "$#" -eq 4 ] && [ "$1" = api ] &&
    [ "$2" = "repos/$REPO/collaborators/$authz_lookup_user/permission" ] &&
    [ "$3" = --jq ] && [ "$4" = .permission ] || return 97
  [ "$AUTHZ_GH_STATUS" = 0 ] || return "$AUTHZ_GH_STATUS"
  printf '%s\\n' "$AUTHZ_GH_PERMISSION"
}
"""
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", stub + job_shell(job_blocks(text)["authz"])],
        env={**os.environ, "EVENT": event, "HEAD_REPO": head_repo,
             "REPO": AUTHZ_TEST_REPO, "HEAD_IS_FORK": head_is_fork,
             "ASSOCIATION": association, "PR_AUTHOR": pr_author,
             "WR_HEAD_REPO": wr_head_repo, "WR_ACTOR": wr_actor,
             "GH_TOKEN": "", "ALLOWED_BOTS": allowed_bots[0],
             "GITHUB_STEP_SUMMARY": str(summary), "AUTHZ_GH_LOG": str(lookup_log),
             "AUTHZ_GH_PERMISSION": permission, "AUTHZ_GH_STATUS": str(lookup_status)},
        capture_output=True, text=True,
    )
    assert result.returncode == (0 if expected else 1), (
        "authz shell decision disagrees with the required event/identity verdict: "
        + result.stdout + result.stderr
    )
    assert result.stderr == ""
    summary_text = summary.read_text(encoding="utf-8")
    if expected:
        by_author = f" by {pr_author}" if pr_author else ""
        assert result.stdout == ""
        assert summary_text == f"authz: allowed ({event}{by_author})\n"
    else:
        assert result.stdout.startswith("::error title=authz::")
        assert summary_text.startswith("authz: REFUSED: ")
        assert "authz: allowed" not in summary_text
    lookup_user = pr_author if event == "pull_request" else wr_actor
    expected_lookups = (
        [f"api repos/{AUTHZ_TEST_REPO}/collaborators/{lookup_user}/permission --jq .permission"]
        if lookup_expected else []
    )
    assert lookup_log.read_text(encoding="utf-8").splitlines() == expected_lookups


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("head_repo,head_is_fork,association,pr_author,expected,lookup_expected", [
    (AUTHZ_TEST_REPO, "false", "OWNER", "owner", True, False),
    (AUTHZ_TEST_REPO, "false", "MEMBER", "member", True, False),
    (AUTHZ_TEST_REPO, "false", "COLLABORATOR", "collaborator", True, False),
    (AUTHZ_TEST_REPO, "false", "NONE", "dependabot[bot]", True, False),
    (AUTHZ_TEST_REPO, "false", "NONE", "pineforge-app[bot]", True, False),
    (AUTHZ_TEST_REPO, "false", "NONE", "outsider", False, True),
    (AUTHZ_TEST_REPO, "false", "CONTRIBUTOR", "outsider", False, True),
    (AUTHZ_TEST_REPO, "false", "NONE", "other[bot]", False, True),
    (AUTHZ_TEST_REPO, "false", "NONE", "not-pineforge-app[bot]", False, True),
    (AUTHZ_TEST_REPO, "false", "", "", False, True),
    ("outsider/fork", "true", "NONE", "outsider", False, False),
    ("member/fork", "true", "MEMBER", "member", False, False),
    ("outsider/fork", "true", "NONE", "dependabot[bot]", False, False),
    (AUTHZ_TEST_REPO, "true", "MEMBER", "member", False, False),
    (AUTHZ_TEST_REPO, "", "MEMBER", "member", False, False),
    ("", "false", "MEMBER", "member", False, False),
])
def test_authz_shell_checks_pr_repository_fork_and_author(
    filename, tmp_path, head_repo, head_is_fork, association, pr_author,
    expected, lookup_expected,
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_authz_shell(
        text, tmp_path, head_repo=head_repo, head_is_fork=head_is_fork,
        association=association, pr_author=pr_author, expected=expected,
        lookup_expected=lookup_expected,
    )


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("association,permission,lookup_status,expected", [
    ("CONTRIBUTOR", "write", 0, True),
    ("CONTRIBUTOR", "admin", 0, True),
    ("CONTRIBUTOR", "maintain", 0, True),
    ("CONTRIBUTOR", "read", 0, False),
    ("CONTRIBUTOR", "triage", 0, False),
    ("CONTRIBUTOR", "none", 0, False),
    ("CONTRIBUTOR", "", 0, False),
    ("CONTRIBUTOR", "write", 1, False),
    ("NONE", "write", 0, True),
    ("NONE", "read", 0, False),
])
def test_authz_shell_checks_pr_permission_and_failed_lookup(
    filename, tmp_path, association, permission, lookup_status, expected
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_authz_shell(
        text, tmp_path, association=association, pr_author="private-member",
        permission=permission, lookup_status=lookup_status,
        expected=expected, lookup_expected=True,
    )


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("event,expected", [
    ("push", True), ("workflow_dispatch", True), ("schedule", True),
    ("merge_group", True), ("release", True), ("workflow_call", True),
    ("repository_dispatch", True), ("pull_request_target", False),
    ("check_run", False), ("unknown_event", False), ("", False),
])
def test_authz_shell_accepts_only_the_canonical_non_pr_events(filename, tmp_path, event, expected):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_authz_shell(
        text, tmp_path, event=event, head_repo="", head_is_fork="",
        association="", pr_author="", expected=expected,
    )


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("wr_head_repo,wr_actor,permission,lookup_status,expected,lookup_expected", [
    (AUTHZ_TEST_REPO, "member", "write", 0, True, True),
    (AUTHZ_TEST_REPO, "owner", "admin", 0, True, True),
    (AUTHZ_TEST_REPO, "maintainer", "maintain", 0, True, True),
    (AUTHZ_TEST_REPO, "outsider", "read", 0, False, True),
    (AUTHZ_TEST_REPO, "outsider", "triage", 0, False, True),
    (AUTHZ_TEST_REPO, "outsider", "none", 0, False, True),
    (AUTHZ_TEST_REPO, "outsider", "", 0, False, True),
    (AUTHZ_TEST_REPO, "member", "write", 1, False, True),
    (AUTHZ_TEST_REPO, "", "none", 0, False, True),
    (AUTHZ_TEST_REPO, "dependabot[bot]", "none", 1, True, False),
    (AUTHZ_TEST_REPO, "pineforge-app[bot]", "none", 1, True, False),
    (AUTHZ_TEST_REPO, "other[bot]", "read", 0, False, True),
    ("outsider/fork", "member", "write", 0, False, False),
    ("outsider/fork", "dependabot[bot]", "write", 0, False, False),
    ("outsider/fork", "pineforge-app[bot]", "write", 0, False, False),
    ("", "member", "write", 0, False, False),
])
def test_authz_shell_checks_workflow_run_head_actor_and_failed_permission_lookup(
    filename, tmp_path, wr_head_repo, wr_actor, permission, lookup_status,
    expected, lookup_expected,
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_authz_shell(
        text, tmp_path, event="workflow_run", head_repo="", head_is_fork="",
        association="", pr_author="", wr_head_repo=wr_head_repo, wr_actor=wr_actor,
        permission=permission, lookup_status=lookup_status,
        expected=expected, lookup_expected=lookup_expected,
    )


@pytest.mark.parametrize("filename", CI_CONDITIONS)
@pytest.mark.parametrize("mutation,case", [
    ("fail-no-op", {"event": "pull_request_target", "expected": False}),
    ("drop-fork-check", {"head_is_fork": "true", "expected": False}),
    ("drop-head-repo-check", {"head_repo": "outsider/fork", "expected": False}),
    ("drop-default-arm", {"event": "unknown_event", "expected": False}),
    ("allow-target-event", {"event": "pull_request_target", "expected": False}),
    ("allow-unaffiliated-author", {"association": "NONE", "expected": False}),
    ("change-allowed-bots", {"association": "NONE", "pr_author": "other[bot]", "expected": False}),
    ("drop-workflow-run-arm", {
        "event": "workflow_run", "wr_head_repo": AUTHZ_TEST_REPO, "wr_actor": "member",
        "permission": "write", "expected": True, "lookup_expected": True,
    }),
    ("drop-workflow-run-head-check", {
        "event": "workflow_run", "wr_head_repo": "outsider/fork",
        "wr_actor": "pineforge-app[bot]", "expected": False,
    }),
    ("drop-workflow-run-lookup", {
        "event": "workflow_run", "wr_head_repo": AUTHZ_TEST_REPO, "wr_actor": "outsider",
        "permission": "read", "expected": False, "lookup_expected": True,
    }),
    ("lookup-fails-open", {
        "event": "workflow_run", "wr_head_repo": AUTHZ_TEST_REPO, "wr_actor": "member",
        "permission": "write", "lookup_status": 1, "expected": False, "lookup_expected": True,
    }),
    ("allow-read-permission", {
        "event": "workflow_run", "wr_head_repo": AUTHZ_TEST_REPO, "wr_actor": "outsider",
        "permission": "read", "expected": False, "lookup_expected": True,
    }),
    ("drop-pr-lookup", {
        "association": "CONTRIBUTOR", "pr_author": "private-member",
        "permission": "write", "expected": True, "lookup_expected": True,
    }),
    ("pr-lookup-fails-open", {
        "association": "CONTRIBUTOR", "pr_author": "private-member",
        "permission": "write", "lookup_status": 1, "expected": False, "lookup_expected": True,
    }),
    ("pr-allow-read-permission", {
        "association": "CONTRIBUTOR", "pr_author": "outsider",
        "permission": "read", "expected": False, "lookup_expected": True,
    }),
    ("stale-authz-job", {
        "association": "CONTRIBUTOR", "pr_author": "private-member",
        "permission": "write", "expected": True, "lookup_expected": True,
    }),
])
def test_authz_shell_refuses_mutations_that_change_identity_or_event_decisions(
    filename, tmp_path, mutation, case
):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = mutate_authz_job(text, mutation)
    with pytest.raises(AssertionError, match="authz shell decision"):
        assert_authz_shell(mutant, tmp_path, **case)


def assert_gate_result_bindings(text):
    step = named_step(job_blocks(text)["gate"], "Check gate results")
    for key, value in GATE_ENV.items():
        assert step.count(f"          {key}: {value}\n") == 1, (
            f"gate must bind {key} to its actual need"
        )


def assert_gate_shell(text, authz, preflight, differential, pytest_result, docs_only):
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         job_shell(named_step(job_blocks(text)["gate"], "Check gate results"))],
        env={**os.environ, "AUTHZ_RESULT": authz, "PREFLIGHT_RESULT": preflight,
             "DIFFERENTIAL_RESULT": differential, "PYTEST_RESULT": pytest_result,
             "DOCS_ONLY": docs_only},
        capture_output=True, text=True,
    )
    accepted_sweeps = (
        differential == pytest_result == "skipped"
        if docs_only == "true" else differential == pytest_result == "success"
    )
    expected = authz == preflight == "success" and accepted_sweeps
    assert (result.returncode == 0) == expected, (
        "gate shell accepted a non-success need or rejected the existing docs rule: "
        + result.stdout + result.stderr
    )


@pytest.mark.parametrize("authz", JOB_RESULTS)
@pytest.mark.parametrize("preflight", JOB_RESULTS)
@pytest.mark.parametrize("differential", JOB_RESULTS)
@pytest.mark.parametrize("pytest_result", JOB_RESULTS)
@pytest.mark.parametrize("docs_only", ["true", "false"])
def test_gate_shell_requires_authz_success_and_preserves_docs_only_results(
    authz, preflight, differential, pytest_result, docs_only
):
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    assert_gate_shell(text, authz, preflight, differential, pytest_result, docs_only)


@pytest.mark.parametrize("key", GATE_ENV)
def test_gate_policy_refuses_a_missing_or_forged_need_binding(key):
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    original = f"          {key}: {GATE_ENV[key]}\n"
    for replacement in ("", f"          {key}: success\n"):
        mutant = replace_step_text(text, "gate", "Check gate results", original, replacement)
        with pytest.raises(AssertionError, match=f"gate must bind {key}"):
            assert_ci_workflow(mutant, "gate.yml")


@pytest.mark.parametrize("bad_result", ["skipped", "failure", "cancelled"])
@pytest.mark.parametrize("docs_only,sweep_result", [("true", "skipped"), ("false", "success")])
def test_gate_shell_refuses_mutations_accepting_a_non_success_authz(
    bad_result, docs_only, sweep_result
):
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    original = (
        '          if [ "$AUTHZ_RESULT" = success ] && [ "$PREFLIGHT_RESULT" = success ]; then\n'
    )
    replacements = [
        '          if [ "$PREFLIGHT_RESULT" = success ]; then\n',
        '          if { [ "$AUTHZ_RESULT" = success ] || '
        f'[ "$AUTHZ_RESULT" = {bad_result} ]; }} && [ "$PREFLIGHT_RESULT" = success ]; then\n',
    ]
    for replacement in replacements:
        mutant = replace_job_text(text, "gate", original, replacement)
        with pytest.raises(AssertionError, match="gate shell accepted"):
            assert_gate_shell(
                mutant, bad_result, "success", sweep_result, sweep_result, docs_only
            )


def test_gate_shell_refuses_mutations_that_hide_any_upstream_failure():
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    mutant = replace_job_text(
        text, "gate", '          [ "$state" = success ]\n', "          true\n"
    )
    with pytest.raises(AssertionError, match="gate shell accepted"):
        assert_gate_shell(mutant, "success", "failure", "success", "success", "false")


def test_gate_aggregate_reports_after_cancellation():
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    # Pin the actual job independently of GATE_CONDITION: a cancelled run
    # must still reach the aggregate, because GitHub accepts skipped checks.
    assert job_values(job_blocks(text)["gate"], "if") == ["${{ always() }}"]
    assert_gate_context(text, gate_workflow=True)
    assert_ci_workflow(text, "gate.yml")


@pytest.mark.parametrize("bad_condition", [
    None,
    "!cancelled()",
    "${{ !cancelled() }}",
    "success()",
    "${{ success() }}",
    "failure()",
    "${{ failure() }}",
    "always()",
    f"always() && {AUTHZ_SUCCESS}",
    "${{ always() && needs.authz.result == 'success' }}",
    f"!cancelled() && {AUTHZ_SUCCESS}",
    f"always() && ({FORBIDDEN_FORK_GUARD})",
])
def test_gate_context_refuses_conditions_that_can_skip_the_required_aggregate(bad_condition):
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    mutant = replace_job_condition(text, "gate", bad_condition)
    with pytest.raises(AssertionError, match=r"aggregate must use exactly \$\{\{ always\(\) \}\}"):
        assert_gate_context(mutant, gate_workflow=True)
    with pytest.raises(AssertionError):
        assert_ci_workflow(mutant, "gate.yml")


@pytest.mark.parametrize("filename,job_id", [
    ("gate.yml", "differential"), ("gate.yml", "pytest"), ("test.yml", "pytest"),
])
def test_ci_policy_refuses_dropping_the_docs_only_condition(filename, job_id):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = replace_job_condition(text, job_id, None)
    with pytest.raises(AssertionError, match="existing job conditions"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
def test_ci_policy_refuses_a_target_trigger(filename):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert "\non:\n" in text
    mutant = text.replace("\non:\n", "\non:\n  pull_request_target: {}\n", 1)
    with pytest.raises(AssertionError, match="pull_request_target"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename,job_id", CI_JOBS)
def test_ci_policy_refuses_a_target_condition(filename, job_id):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = replace_job_condition(text, job_id, "github.event_name != 'pull_request_target'")
    with pytest.raises(AssertionError, match="pull_request_target"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename,job_id", CI_JOBS)
@pytest.mark.parametrize("actor", ["actor", "triggering_actor"])
def test_ci_policy_refuses_dependabot_exclusions(filename, job_id, actor):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    condition = CI_CONDITIONS[filename][job_id]
    mutant = replace_job_condition(
        text, job_id,
        f"({condition or 'true'}) && github.{actor} != 'dependabot[bot]'",
    )
    with pytest.raises(AssertionError, match="Dependabot"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename,job_id", CI_JOBS)
def test_ci_policy_refuses_the_fragment_environment(filename, job_id):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    body = job_blocks(text)[job_id]
    if "\n    env:\n" in body:
        original = "\n    env:\n"
        replacement = original + f"      PF_CI_LABELS: '{CI_RUNNER}'\n"
    else:
        original = f"    runs-on: {ci_runner(job_id)}\n"
        replacement = original + f"    env:\n      PF_CI_LABELS: '{CI_RUNNER}'\n"
    mutant = replace_job_text(text, job_id, original, replacement)
    with pytest.raises(AssertionError, match="PF_CI_LABELS"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename", CI_CONDITIONS)
def test_ci_policy_refuses_an_unclassified_new_job(filename):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    mutant = text + (
        f"\n  unguarded:\n    runs-on: {CI_RUNNER}\n"
        "    steps:\n      - run: pwd\n"
    )
    with pytest.raises(AssertionError, match="job inventory"):
        assert_ci_workflow(mutant, filename)


@pytest.mark.parametrize("filename,job_id", CI_JOBS)
@pytest.mark.parametrize("credential_setting", ["oidc", "environment", "secret"])
def test_ci_policy_refuses_credentials_on_the_pool(filename, job_id, credential_setting):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    body = job_blocks(text)[job_id]
    original = f"    runs-on: {ci_runner(job_id)}\n"
    if credential_setting == "oidc":
        if "\n    permissions: {}\n" in body:
            original = "\n    permissions: {}\n"
            replacement = "\n    permissions:\n      id-token: write\n"
        elif "\n    permissions:\n" in body:
            original = "\n    permissions:\n"
            replacement = original + "      id-token: write\n"
        else:
            replacement = original + "    permissions:\n      id-token: write\n"
    elif credential_setting == "secret":
        if "\n    env:\n" in body:
            original = "\n    env:\n"
            replacement = original + "      DEPLOY_SECRET: ${{ secrets.DEPLOY_SECRET }}\n"
        else:
            replacement = original + "    env:\n      DEPLOY_SECRET: ${{ secrets.DEPLOY_SECRET }}\n"
    else:
        replacement = original + "    environment: production\n"
    mutant = replace_job_text(text, job_id, original, replacement)
    with pytest.raises(AssertionError, match="credential jobs"):
        assert_ci_workflow(mutant, filename)


def assert_credential_workflow(text, filename):
    assert not re.search(r"\bpull_request\b", workflow_trigger(text)), (
        "credential/release jobs must not run on pull_request"
    )
    jobs = job_blocks(text)
    assert set(jobs) == set(CREDENTIAL_RUNNERS[filename]), (
        "credential job inventory must stay explicit"
    )
    for job_id, expected in CREDENTIAL_RUNNERS[filename].items():
        assert job_values(jobs[job_id], "runs-on") == [expected], (
            f"{job_id}: credential/release jobs untouched: pending pf-ci-release"
        )


@pytest.mark.parametrize("filename", CREDENTIAL_RUNNERS)
def test_credential_workflows_keep_their_existing_runners(filename):
    assert_credential_workflow((WORKFLOWS / filename).read_text(encoding="utf-8"), filename)


@pytest.mark.parametrize("filename,job_id", CREDENTIAL_JOBS)
@pytest.mark.parametrize("bad_runner", [
    CI_RUNNER,
    '["self-hosted","pf-ci","pf-ci-x64-16"]',
    '["self-hosted","pf-ci","pf-ci-crj"]',
    '["self-hosted","pf-ci","pf-ci-release"]',
])
def test_credential_policy_refuses_a_runner_move(filename, job_id, bad_runner):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    original = CREDENTIAL_RUNNERS[filename][job_id]
    mutant = replace_job_text(
        text, job_id, f"    runs-on: {original}\n", f"    runs-on: {bad_runner}\n"
    )
    with pytest.raises(AssertionError, match="pending pf-ci-release"):
        assert_credential_workflow(mutant, filename)


@pytest.mark.parametrize("filename", CREDENTIAL_RUNNERS)
def test_credential_policy_refuses_a_pr_trigger_or_unclassified_job(filename):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert "\non:\n" in text
    mutant = text.replace("\non:\n", "\non:\n  pull_request: {}\n", 1)
    with pytest.raises(AssertionError, match="must not run on pull_request"):
        assert_credential_workflow(mutant, filename)
    mutant = text + (
        "\n  extra:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: pwd\n"
    )
    with pytest.raises(AssertionError, match="job inventory"):
        assert_credential_workflow(mutant, filename)


def assert_pr_concurrency(workflow, prefix, cancel_when):
    assert (
        f"concurrency:\n  group: {prefix}-"
        "${{ github.event.pull_request.number || github.run_id }}\n"
        f"  cancel-in-progress: ${{{{ {cancel_when} }}}}\n"
    ) in workflow
    assert "cancel-in-progress: true" not in workflow
    assert "|| github.ref }}" not in workflow


@pytest.mark.parametrize("filename", CONCURRENCY_RULES)
def test_concurrency_cancels_only_pull_requests(filename):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_pr_concurrency(workflow, *CONCURRENCY_RULES[filename])


@pytest.mark.parametrize("filename,bad_cancel", [
    ("gate.yml", "${{ github.event_name == 'pull_request_target' }}"),
    ("gate.yml", "true"),
    (
        "gate.yml",
        "${{ github.event_name == 'pull_request' || "
        "github.event_name == 'pull_request_target' }}",
    ),
    (
        "gate.yml",
        "${{ github.event_name == 'pull_request' || "
        "github.event_name == 'pull_request_target' || github.event_name == 'push' }}",
    ),
    ("test.yml", "true"),
    (
        "test.yml",
        "${{ github.event_name == 'pull_request' || github.event_name == 'push' }}",
    ),
])
def test_concurrency_guard_rejects_cancel_rule_mutations(filename, bad_cancel):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    prefix, cancel_when = CONCURRENCY_RULES[filename]
    original = f"  cancel-in-progress: ${{{{ {cancel_when} }}}}\n"
    assert original in workflow
    mutant = workflow.replace(original, f"  cancel-in-progress: {bad_cancel}\n", 1)
    with pytest.raises(AssertionError):
        assert_pr_concurrency(mutant, prefix, cancel_when)


@pytest.mark.parametrize("filename", CONCURRENCY_RULES)
@pytest.mark.parametrize("bad_group", [
    "${{ github.event.pull_request.number || github.ref }}",
    "${{ github.sha }}",
])
def test_concurrency_guard_rejects_shared_non_pr_groups(filename, bad_group):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    original = "${{ github.event.pull_request.number || github.run_id }}"
    assert original in workflow
    mutant = workflow.replace(original, bad_group, 1)
    with pytest.raises(AssertionError):
        assert_pr_concurrency(mutant, *CONCURRENCY_RULES[filename])
