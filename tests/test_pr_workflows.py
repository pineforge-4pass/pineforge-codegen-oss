"""Protect workflow concurrency, the trusted gate status and promotion authz."""
import json
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
CONCURRENCY_RULES = {
    "test.yml": ("test", "github.event_name == 'pull_request'"),
    "gate.yml": (
        "gate",
        "github.event_name == 'pull_request' || "
        "github.event_name == 'pull_request_target'",
    ),
}


def job_blocks(text):
    """Read the top-level jobs in the repository's block-style workflows."""
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(
        r"""^  ([\w-]+|'[^']+'|"[^"]+"):[ \t]*(?:#.*)?$""", body, re.M
    ))
    job_lines = re.findall(r"^  [^\s#][^\n]*$", body, re.M)
    assert matches and len(matches) == len(job_lines), (
        "workflow jobs must use the checked block format"
    )
    return {
        match[1].strip("'\""): body[
            match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)
        ]
        for index, match in enumerate(matches)
    }


def assert_no_pr_gate(text):
    trigger = re.search(r"""^(?:on|'on'|"on"):[^\n]*(?:\n(?:[ \t]+[^\n]*|))*""", text, re.M)
    assert trigger is not None, "workflow must declare its trigger"
    if not re.search(r"\bpull_request\b", trigger[0]):
        return
    for job_id, body in job_blocks(text).items():
        assert job_id != "gate", "pull_request must not publish the trusted gate check"
        name = re.search(r"^    name:[ \t]*([^\n]+)", body, re.M)
        if name:
            assert name[1].split("#", 1)[0].strip().strip("'\"") != "gate", (
                "pull_request must not publish the trusted gate check"
            )


def test_no_pull_request_workflow_can_claim_the_gate_context():
    for workflow in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        assert_no_pr_gate(workflow.read_text(encoding="utf-8"))


@pytest.mark.parametrize("trigger", [
    "on:\n  pull_request:\n    branches: [main]",
    "on: [push, pull_request]",
    "on: pull_request",
    "'on':\n  'pull_request': {}",
])
@pytest.mark.parametrize("job", [
    "  gate:\n    runs-on: ubuntu-24.04",
    "  'gate':\n    runs-on: ubuntu-24.04",
    '  "gate":\n    runs-on: ubuntu-24.04',
    "  tests:\n    name: gate\n    runs-on: ubuntu-24.04",
    "  tests:\n    name: 'gate'\n    runs-on: ubuntu-24.04",
    '  tests:\n    name: "gate" # reserved\n    runs-on: ubuntu-24.04',
])
def test_gate_context_guard_rejects_reserved_ids_and_names(trigger, job):
    with pytest.raises(AssertionError, match="trusted gate check"):
        assert_no_pr_gate(f"{trigger}\n\njobs:\n{job}\n")


def test_gate_context_guard_leaves_the_trusted_target_workflow():
    assert_no_pr_gate("on:\n  pull_request_target:\n\njobs:\n  gate:\n    runs-on: ubuntu-24.04\n")


def assert_pr_concurrency(workflow, prefix, cancel_when):
    assert (
        f"concurrency:\n  group: {prefix}-"
        "${{ github.event.pull_request.number || github.run_id }}\n"
        f"  cancel-in-progress: ${{{{ {cancel_when} }}}}\n"
    ) in workflow
    assert "cancel-in-progress: true" not in workflow
    assert "|| github.ref }}" not in workflow


def assert_test_workflow_has_canonical_trigger(workflow):
    trigger = re.search(
        r"""^(?:on|'on'|"on"):[^\n]*(?:\n(?:[ \t]+[^\n]*|))*""",
        workflow,
        re.M,
    )
    assert trigger is not None, "test workflow must declare its trigger"
    assert re.findall(r"^  ([\w-]+):", trigger[0], re.M) == [
        "push", "workflow_dispatch",
    ], "test workflow must not run on pull_request"


def test_test_workflow_has_no_pull_request_trigger():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_test_workflow_has_canonical_trigger(workflow)


def test_test_workflow_trigger_mutation_is_rejected():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    original = "  workflow_dispatch:\n"
    assert workflow.count(original) == 1
    mutant = workflow.replace(original, "  pull_request:\n" + original, 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="must not run on pull_request"):
        assert_test_workflow_has_canonical_trigger(mutant)


def assert_test_workflow_runner_routing(workflow):
    assert_test_workflow_has_canonical_trigger(workflow)
    runners = {
        name: [line for line in body.splitlines() if line.startswith("    runs-on:")]
        for name, body in job_blocks(workflow).items()
    }
    assert runners == {
        "authz": ["    runs-on: ubuntu-24.04"],
        "changes": ["    runs-on: ubuntu-24.04"],
        "workflow-lint": ["    runs-on: gke-8cpu-16gb-amd64"],
        "package-files": ["    runs-on: gke-8cpu-16gb-amd64"],
        "pytest": ["    runs-on: gke-8cpu-16gb-amd64"],
        "test-check": ["    runs-on: ubuntu-24.04"],
    }, "test.yml runner routing differs"


def test_test_workflow_routes_only_push_dispatch_heavy_jobs_to_pods():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_test_workflow_runner_routing(workflow)


@pytest.mark.parametrize("name,before,after", [
    ("workflow-lint", "gke-8cpu-16gb-amd64", "ubuntu-24.04"),
    ("package-files", "gke-8cpu-16gb-amd64", "ubuntu-24.04"),
    ("pytest", "gke-8cpu-16gb-amd64", "ubuntu-24.04"),
    ("authz", "ubuntu-24.04", "gke-8cpu-16gb-amd64"),
    ("changes", "ubuntu-24.04", "gke-8cpu-16gb-amd64"),
    ("test-check", "ubuntu-24.04", "gke-8cpu-16gb-amd64"),
    ("pytest", "gke-8cpu-16gb-amd64", "gke-4cpu-8gb-amd64"),
])
def test_test_workflow_runner_mutations_are_rejected(name, before, after):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_test_workflow_runner_routing(workflow)
    body = job_blocks(workflow)[name]
    original = f"    runs-on: {before}\n"
    assert body.count(original) == 1
    changed = body.replace(original, f"    runs-on: {after}\n", 1)
    mutant = workflow.replace(body, changed, 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="test.yml runner routing differs"):
        assert_test_workflow_runner_routing(mutant)


def assert_test_jobs_keep_canonical_conditions(workflow):
    jobs = job_blocks(workflow)
    for name in ("changes", "package-files"):
        assert not re.search(r"^    if:", jobs[name], re.M), (
            f"{name}: canonical job must not have a job-level condition"
        )
    assert re.search(
        r"^    if: needs\.changes\.outputs\.code == 'true'$",
        jobs["pytest"],
        re.M,
    )
    assert re.search(r"^    if: \$\{\{ always\(\) \}\}$", jobs["test-check"], re.M), (
        "test-check: the aggregate must keep exactly `if: ${{ always() }}`")
    for name in ("changes", "package-files", "pytest", "test-check"):
        assert not re.search(
            r"^    if:.*github\.event_name\s*!=\s*'pull_request'",
            jobs[name],
            re.M,
        ), f"{name}: canonical job must not carry a pull_request guard"


def test_test_workflow_jobs_keep_canonical_conditions():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_test_jobs_keep_canonical_conditions(workflow)


@pytest.mark.parametrize("filename", CONCURRENCY_RULES)
def test_concurrency_cancels_only_pull_requests(filename):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_pr_concurrency(workflow, *CONCURRENCY_RULES[filename])


@pytest.mark.parametrize("filename,bad_cancel", [
    ("gate.yml", "${{ github.event_name == 'pull_request' }}"),
    ("gate.yml", "true"),
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


def workflow_run_scripts(workflow, job_id):
    """Extract the block-style run scripts from one shipped workflow job."""
    body = job_blocks(workflow)[job_id]
    return [
        textwrap.dedent(script)
        for script in re.findall(
            r"^        run: \|\n((?:          [^\n]*\n|\n)+)", body, re.M
        )
    ]


def authz_script(workflow):
    scripts = workflow_run_scripts(workflow, "authz")
    assert len(scripts) == 1, "test authz must have exactly one inline run script"
    return scripts[0]


def assert_package_file_checks(workflow):
    jobs = job_blocks(workflow)
    assert "package-files" in jobs, "test must have its canonical package-files light job"
    package = jobs["package-files"]
    assert re.search(r"^    needs: \[authz\]$", package, re.M)
    assert [line for line in package.splitlines() if line.startswith("    runs-on:")] == [
        "    runs-on: gke-8cpu-16gb-amd64",
    ]
    assert not re.search(r"^    if:", package, re.M), (
        "package-files must not carry a job-level pull_request guard"
    )
    step_lines = package.splitlines()
    step_starts = [
        index for index, line in enumerate(step_lines)
        if line.startswith("      - ")
    ]
    assert step_starts, "package-files must define steps"
    for index, start in enumerate(step_starts):
        end = step_starts[index + 1] if index + 1 < len(step_starts) else len(step_lines)
        step = "\n".join(step_lines[start:end])
        assert '        if: "${{ needs.authz.result == \'success\' }}"' in step
    assert "test -f LICENSE" in package
    assert "test -f README.md" in package
    assert re.search(
        r"^        run: python -m pytest -q tests/test_license_entity\.py$", package, re.M,
    ), "package-files must execute the entity test"
    assert "needs: [authz, changes, workflow-lint, package-files, pytest]" in jobs["test-check"]


def test_test_workflow_runs_package_file_and_entity_checks_on_every_diff():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_package_file_checks(workflow)


def test_package_file_checks_reject_echoed_entity_test():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    original = "        run: python -m pytest -q tests/test_license_entity.py\n"
    assert workflow.count(original) == 1, "mutation must find the entity test command exactly once"
    mutant = workflow.replace(original, "        run: echo tests/test_license_entity.py\n", 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="package-files must execute the entity test"):
        assert_package_file_checks(mutant)


def test_pytest_checks_out_the_pull_request_head():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    pytest_job = job_blocks(workflow)["pytest"]
    assert 'ref: "${{ github.event.pull_request.head.sha || github.sha }}"' in pytest_job
    assert "ref: ${{ github.sha }}" not in pytest_job


@dataclass(frozen=True)
class AuthzCase:
    name: str
    env: dict[str, str]
    expected_exit: int
    refusal: str = ""
    lookup_target: str = ""
    permission: str = "none"
    lookup_exit: int = 0


AUTHZ_CASES = [
    AuthzCase("push", {"EVENT": "push"}, 0),
    AuthzCase(
        "trusted-pull-request",
        {"EVENT": "pull_request", "ASSOCIATION": "OWNER"},
        0,
    ),
    AuthzCase(
        "foreign-pull-request",
        {"EVENT": "pull_request", "HEAD_REPO": "outsider/pineforge-codegen-oss"},
        1,
        "pull request from another repository (outsider/pineforge-codegen-oss)",
    ),
    AuthzCase(
        "fork-flag",
        {"EVENT": "pull_request", "HEAD_IS_FORK": "true"},
        1,
        "pull request from a fork",
    ),
    AuthzCase(
        "contributor-write",
        {"EVENT": "pull_request", "ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "writer"},
        0,
        lookup_target="author",
        permission="write",
    ),
    AuthzCase(
        "contributor-read",
        {"EVENT": "pull_request", "ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "reader"},
        1,
        "author reader (CONTRIBUTOR) has permission 'read', not write, and is not an allowlisted bot",
        lookup_target="author",
        permission="read",
    ),
    AuthzCase(
        "allowlisted-pull-request-bot",
        {
            "EVENT": "pull_request",
            "ASSOCIATION": "NONE",
            "PR_AUTHOR": "dependabot[bot]",
        },
        0,
    ),
    AuthzCase(
        "workflow-run-app-bot",
        {
            "EVENT": "workflow_run",
            "WR_HEAD_REPO": "pineforge-4pass/pineforge-codegen-oss",
            "WR_ACTOR": "pineforge-app[bot]",
        },
        0,
    ),
    AuthzCase(
        "workflow-run-foreign-head",
        {
            "EVENT": "workflow_run",
            "WR_HEAD_REPO": "outsider/pineforge-codegen-oss",
        },
        1,
        "workflow_run head repository does not match this repository",
    ),
    AuthzCase(
        "workflow-run-unlisted-bot",
        {
            "EVENT": "workflow_run",
            "WR_HEAD_REPO": "pineforge-4pass/pineforge-codegen-oss",
            "WR_ACTOR": "other[bot]",
        },
        1,
        "workflow_run bot is not on the canon allowlist",
    ),
    AuthzCase(
        "workflow-run-writer",
        {
            "EVENT": "workflow_run",
            "WR_HEAD_REPO": "pineforge-4pass/pineforge-codegen-oss",
            "WR_ACTOR": "writer",
        },
        0,
        lookup_target="workflow",
        permission="write",
    ),
    AuthzCase(
        "unknown-event",
        {"EVENT": "unexpected"},
        1,
        "event unexpected does not run CI (pull_request_target and other events are not allowed)",
    ),
]


def assert_authz_case(workflow, case, tmp_path):
    script = authz_script(workflow)
    bash = shutil.which("bash")
    if bash is None:
        pytest.fail("NOT RUN: test authz decision table requires bash on the test runner")
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{bash}\n"
        'printf "%s\\n" "$*" >> "$GH_CALLS"\n'
        '[ "$#" -eq 4 ] && [ "$1" = api ] && '
        '[ "$3" = --jq ] && [ "$4" = .permission ] || exit 97\n'
        'printf "%s\\n" "$GH_PERMISSION"\n'
        'exit "$GH_LOOKUP_EXIT"\n',
        encoding="utf-8",
    )
    gh.chmod(0o700)
    summary_path = tmp_path / "summary"
    calls_path = tmp_path / "gh-calls"
    env = {
        "EVENT": "pull_request",
        "REPO": "pineforge-4pass/pineforge-codegen-oss",
        "HEAD_REPO": "pineforge-4pass/pineforge-codegen-oss",
        "HEAD_IS_FORK": "false",
        "ASSOCIATION": "MEMBER",
        "PR_AUTHOR": "member",
        "WR_HEAD_REPO": "pineforge-4pass/pineforge-codegen-oss",
        "WR_ACTOR": "writer",
        "GH_TOKEN": "",
        "ALLOWED_BOTS": "dependabot[bot] pineforge-app[bot]",
        **case.env,
        "PATH": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
        "GH_CALLS": str(calls_path),
        "GH_PERMISSION": case.permission,
        "GH_LOOKUP_EXIT": str(case.lookup_exit),
    }
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == case.expected_exit, (
        f"{case.name}: expected exit {case.expected_exit}, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stderr == "", f"{case.name}: unexpected shell error: {result.stderr}"
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
    if case.expected_exit:
        assert result.stdout == f"::error title=authz::{case.refusal}\n", case.name
        assert summary == f"authz: REFUSED: {case.refusal}\n", case.name
    else:
        author = f" by {env['PR_AUTHOR']}" if env["PR_AUTHOR"] else ""
        assert result.stdout == "", case.name
        assert summary == f"authz: allowed ({env['EVENT']}{author})\n", case.name
    if case.lookup_target:
        actor = env["PR_AUTHOR"] if case.lookup_target == "author" else env["WR_ACTOR"]
        expected_calls = [
            f"api repos/{env['REPO']}/collaborators/{actor}/permission --jq .permission"
        ]
    else:
        expected_calls = []
    assert calls == expected_calls, f"{case.name}: unexpected permission lookups: {calls}"


@pytest.mark.parametrize("case", AUTHZ_CASES, ids=[case.name for case in AUTHZ_CASES])
def test_test_authz_executes_workflow_decision_table(case, tmp_path):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_authz_case(workflow, case, tmp_path)


def test_test_authz_rejects_noop_fail_mutation(tmp_path):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    original = (
        '          fail() { echo "::error title=authz::$1"; '
        'echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }\n'
    )
    assert workflow.count(original) == 1, "mutation must find test authz's fail function exactly once"
    mutant = workflow.replace(original, "          fail() { :; }\n", 1)
    assert mutant != workflow
    case = next(case for case in AUTHZ_CASES if case.name == "foreign-pull-request")
    with pytest.raises(AssertionError, match=case.name):
        assert_authz_case(mutant, case, tmp_path)


def aggregate_gate_script(workflow):
    scripts = workflow_run_scripts(workflow, "test-check")
    assert len(scripts) == 3, "test-check must have refusal, classifier and aggregate scripts"
    return scripts[-1]


def assert_aggregate_rejects(
    workflow, pytest_result, tmp_path, *, package_result="success", docs_only="false",
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.fail("NOT RUN: aggregate decision control requires bash on the test runner")
    script = aggregate_gate_script(workflow)
    aggregate = job_blocks(workflow)["test-check"]
    dependencies = re.findall(r"^    needs: \[([\w, -]+)\]$", aggregate, re.M)
    assert len(dependencies) == 1, "test-check must declare one inline dependency list"
    job_results = {"pytest": pytest_result, "package-files": package_result}
    needs = {
        name: {"result": job_results.get(name, "success")}
        for name in (item.strip() for item in dependencies[0].split(","))
    }
    heavy_values = re.findall(r'^          HEAVY_JOBS: ("[^\n]+")$', aggregate, re.M)
    assert len(heavy_values) == 1, "test-check must declare one double-quoted heavy-job list"
    # Decode the shipped YAML's JSON-compatible quoting; the real gate
    # parses the enclosed JSON list itself.
    heavy_jobs = json.loads(heavy_values[0])
    python3 = shutil.which("python3")
    if python3 is None:
        pytest.fail("NOT RUN: aggregate decision control requires python3 on the test runner")
    env = {
        "PATH": str(Path(python3).parent),
        "RUNNER_TEMP": str(tmp_path),
        "NEEDS": json.dumps(needs),
        "HEAVY_JOBS": heavy_jobs,
        "DOCS_ONLY": docs_only,
        "WORKFLOWS_ONLY": "false",
    }
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, (
        f"aggregate must refuse pytest={pytest_result}, package-files={package_result}, "
        f"docs_only={docs_only}; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout == "::error::a needed job did not succeed or was skipped without PR diff proof\n"
    assert result.stderr == ""


def test_test_check_rejects_failed_pytest(tmp_path):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_aggregate_rejects(workflow, "failure", tmp_path)


@pytest.mark.parametrize("package_result", ["failure", "skipped"])
def test_test_check_rejects_unsuccessful_package_files_on_docs_only_diff(package_result, tmp_path):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert_aggregate_rejects(
        workflow, "skipped", tmp_path, package_result=package_result, docs_only="true",
    )
    original = "    needs: [authz, changes, workflow-lint, package-files, pytest]\n"
    replacement = "    needs: [authz, changes, workflow-lint, pytest]\n"
    assert workflow.count(original) == 1, "mutation must find the aggregate dependency list exactly once"
    mutant = workflow.replace(original, replacement, 1)
    assert mutant != workflow
    with pytest.raises(
        AssertionError,
        match=f"aggregate must refuse.*package-files={package_result}, docs_only=true; got 0",
    ):
        assert_aggregate_rejects(
            mutant, "skipped", tmp_path, package_result=package_result, docs_only="true",
        )


def test_test_check_rejects_package_files_added_to_heavy_jobs(tmp_path):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    original = '          HEAVY_JOBS: "[\\"pytest\\"]"\n'
    replacement = '          HEAVY_JOBS: "[\\"package-files\\",\\"pytest\\"]"\n'
    assert workflow.count(original) == 1, "mutation must find the aggregate heavy-job list exactly once"
    mutant = workflow.replace(original, replacement, 1)
    assert mutant != workflow
    with pytest.raises(
        AssertionError,
        match="aggregate must refuse.*package-files=skipped, docs_only=true; got 0",
    ):
        assert_aggregate_rejects(
            mutant, "skipped", tmp_path, package_result="skipped", docs_only="true",
        )


def test_test_check_rejects_aggregate_script_replaced_by_exit_zero(tmp_path):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    script = aggregate_gate_script(workflow)
    original = textwrap.indent(script, " " * 10, lambda _: True)
    source = job_blocks(workflow)["test-check"].rsplit("        run: |\n", 1)[1]
    assert original == source, "aggregate script must preserve the workflow's whitespace"
    assert workflow.count(original) == 1, "mutation must find the aggregate script exactly once"
    mutant = workflow.replace(original, "          exit 0\n", 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="aggregate must refuse.*; got 0"):
        assert_aggregate_rejects(mutant, "failure", tmp_path)


# Independent engine resolver/authz policy, retaining codegen's hosted runner.
# Never derive this pin from the workflow under test.
PROMOTE_GUARD = r'''  promote-guard:
    name: promote-guard
    runs-on: ubuntu-latest
    # Resolution needs read access in private repos; no secrets or OIDC here.
    permissions:
      pull-requests: read
    outputs:
      ok: ${{ steps.authorize.outputs.ok }}
      merge_sha: ${{ steps.pr.outputs.merge_sha }}
      head_sha: ${{ steps.pr.outputs.head_sha }}
      pr_number: ${{ steps.pr.outputs.pr_number }}
      pr_url: ${{ steps.pr.outputs.pr_url }}
      merged_at: ${{ steps.pr.outputs.merged_at }}
    steps:
      - name: Resolve the merged PR
        id: pr
        env:
          GH_TOKEN: ${{ github.token }}
          EVENT_NAME: ${{ github.event_name }}
          REPO: ${{ github.repository }}
          REF: ${{ github.ref }}
          SHA: ${{ github.sha }}
          INPUT_MERGE_SHA: ${{ github.event.inputs.merge_commit }}
          INPUT_HEAD_SHA: ${{ github.event.inputs.head_sha }}
          INPUT_PR_NUMBER: ${{ github.event.inputs.pr_number }}
          INPUT_PR_URL: ${{ github.event.inputs.pr_url }}
          INPUT_MERGED_AT: ${{ github.event.inputs.merged_at }}
        run: |
          set -euo pipefail
          fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }
          [ "$REF" = refs/heads/main ] || fail "baseline promotion requires refs/heads/main"
          case "$EVENT_NAME" in
            workflow_dispatch)
              [[ "$INPUT_MERGE_SHA" =~ ^[0-9a-f]{40}$ ]] ||
                fail "merge_commit must be exactly 40 lowercase hexadecimal characters"
              [[ "$INPUT_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]] ||
                fail "head_sha must be exactly 40 lowercase hexadecimal characters"
              [[ "$INPUT_PR_NUMBER" =~ ^[1-9][0-9]*$ ]] ||
                fail "pr_number must be a positive decimal integer"
              expected_pr_url="https://github.com/$REPO/pull/$INPUT_PR_NUMBER"
              [ "$INPUT_PR_URL" = "$expected_pr_url" ] ||
                fail "pr_url must equal $expected_pr_url"
              [[ "$INPUT_MERGED_AT" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ||
                fail "merged_at must be an ISO-8601 UTC timestamp in YYYY-MM-DDTHH:MM:SSZ form"
              if ! dispatch_pr=$(gh api "repos/$REPO/pulls/$INPUT_PR_NUMBER"); then
                fail "pr_number API lookup failed"
              fi
              [[ -n "${dispatch_pr//[[:space:]]/}" ]] ||
                fail "pr_number API lookup returned an empty response"
              if ! python3 - "$INPUT_PR_NUMBER" 3<<<"$dispatch_pr" <<'PY'
          import json
          import sys

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          try:
              with open(3, encoding="utf-8") as stream:
                  pr = json.load(stream, parse_constant=reject_constant)
          except (OSError, RecursionError, ValueError):
              raise SystemExit(1)
          number = pr.get("number") if isinstance(pr, dict) else None
          if not (type(number) in (int, float) and number == int(sys.argv[1])):
              raise SystemExit(1)
          PY
              then
                fail "pr_number does not match the PR API response"
              fi
              if ! python3 - 3<<<"$dispatch_pr" <<'PY'
          import json

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          try:
              with open(3, encoding="utf-8") as stream:
                  pr = json.load(stream, parse_constant=reject_constant)
          except (OSError, RecursionError, ValueError):
              raise SystemExit(1)
          if not (isinstance(pr, dict) and pr.get("merged") is True):
              raise SystemExit(1)
          PY
              then
                fail "pr_number API record is not merged"
              fi
              if ! python3 - "$INPUT_MERGE_SHA" 3<<<"$dispatch_pr" <<'PY'
          import json
          import sys

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          try:
              with open(3, encoding="utf-8") as stream:
                  pr = json.load(stream, parse_constant=reject_constant)
          except (OSError, RecursionError, ValueError):
              raise SystemExit(1)
          if not (isinstance(pr, dict) and pr.get("merge_commit_sha") == sys.argv[1]):
              raise SystemExit(1)
          PY
              then
                fail "merge_commit does not match the PR merge_commit_sha"
              fi
              if ! python3 - "$INPUT_HEAD_SHA" 3<<<"$dispatch_pr" <<'PY'
          import json
          import sys

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          try:
              with open(3, encoding="utf-8") as stream:
                  pr = json.load(stream, parse_constant=reject_constant)
          except (OSError, RecursionError, ValueError):
              raise SystemExit(1)
          if not (
              isinstance(pr, dict)
              and isinstance(pr.get("head"), dict)
              and pr["head"].get("sha") == sys.argv[1]
          ):
              raise SystemExit(1)
          PY
              then
                fail "head_sha does not match the PR head.sha"
              fi
              if ! python3 - 3<<<"$dispatch_pr" <<'PY'
          import json

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          try:
              with open(3, encoding="utf-8") as stream:
                  pr = json.load(stream, parse_constant=reject_constant)
          except (OSError, RecursionError, ValueError):
              raise SystemExit(1)
          if not (
              isinstance(pr, dict)
              and isinstance(pr.get("base"), dict)
              and pr["base"].get("ref") == "main"
          ):
              raise SystemExit(1)
          PY
              then
                fail "pr_number API record does not use base main"
              fi
              if ! python3 - "$INPUT_MERGED_AT" 3<<<"$dispatch_pr" <<'PY'
          import json
          import sys

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          try:
              with open(3, encoding="utf-8") as stream:
                  pr = json.load(stream, parse_constant=reject_constant)
          except (OSError, RecursionError, ValueError):
              raise SystemExit(1)
          if not (isinstance(pr, dict) and pr.get("merged_at") == sys.argv[1]):
              raise SystemExit(1)
          PY
              then
                fail "merged_at does not match the PR merged_at"
              fi
              if ! dispatch_pulls=$(gh api --paginate --slurp \
                "repos/$REPO/commits/$INPUT_MERGE_SHA/pulls?per_page=100"); then
                fail "merge_commit API lookup failed"
              fi
              [[ -n "${dispatch_pulls//[[:space:]]/}" ]] ||
                fail "merge_commit API lookup returned an empty response"
              if ! python3 - "$INPUT_PR_NUMBER" 3<<<"$dispatch_pulls" <<'PY'
          import json
          import sys

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          try:
              with open(3, encoding="utf-8") as stream:
                  pages = json.load(stream, parse_constant=reject_constant)
          except (OSError, RecursionError, ValueError):
              raise SystemExit(1)
          pulls = (
              [pr for page in pages for pr in page]
              if isinstance(pages, list) and all(isinstance(page, list) for page in pages)
              else []
          )
          if not (
              isinstance(pages, list)
              and all(isinstance(page, list) for page in pages)
              and len(pulls) == 1
              and isinstance(pulls[0], dict)
              and type(pulls[0].get("number")) in (int, float)
              and pulls[0].get("number") == int(sys.argv[1])
          ):
              raise SystemExit(1)
          PY
              then
                fail "merge_commit must list exactly pull request $INPUT_PR_NUMBER"
              fi
              printf '%s\n' "merge_sha=$INPUT_MERGE_SHA" "head_sha=$INPUT_HEAD_SHA" \
                "pr_number=$INPUT_PR_NUMBER" "pr_url=$INPUT_PR_URL" \
                "merged_at=$INPUT_MERGED_AT" "ok=true" >> "$GITHUB_OUTPUT"
              exit 0 ;;
            push) ;;
            *) fail "event $EVENT_NAME does not run baseline promotion (only push and workflow_dispatch are allowed)" ;;
          esac
          # Search every page: a second match must defer, never pick a PR.
          pulls=$(gh api --paginate --slurp \
            "repos/$REPO/commits/$SHA/pulls?per_page=100")
          python3 - 3<<<"$pulls" <<'PY'
          import json
          import os
          import re
          import subprocess

          def skip(reason):
              print(f"::notice::{reason}; skipping")
              with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                  output.write("ok=false\n")

          def fail(reason):
              print(f"::error title=authz::{reason}")
              with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                  summary.write(f"authz: REFUSED: {reason}\n")
              raise SystemExit(1)

          def reject_constant(value):
              raise ValueError(f"invalid JSON constant: {value}")

          def field(obj, *keys):
              for key in keys:
                  obj = obj.get(key) if isinstance(obj, dict) else None
              if (not isinstance(obj, str) or not obj.strip()
                      or any(ord(char) < 32 for char in obj)):
                  fail("empty or unreadable PR field: " + ".".join(keys))
              return obj

          sha = os.environ["SHA"]
          if not re.fullmatch(r"[0-9a-f]{40}", sha):
              fail("empty or unreadable pushed SHA")
          try:
              pages = json.loads(open(3, encoding="utf-8").read(), parse_constant=reject_constant)
          except (ValueError, RecursionError):
              skip("invalid merged PR response JSON")
              raise SystemExit(0)
          if (not isinstance(pages, list)
                  or any(not isinstance(page, list) for page in pages)
                  or any(not isinstance(pr, dict) for page in pages for pr in page)):
              skip("invalid merged PR response page array")
              raise SystemExit(0)
          matches = [pr for page in pages for pr in page
                     if pr.get("state") == "closed"
                     and pr.get("merged_at") is not None
                     and pr.get("merge_commit_sha") == sha
                     and isinstance(pr.get("base"), dict)
                     and pr["base"].get("ref") == "main"]
          if len(matches) != 1:
              skip(f"found {len(matches)} merged PRs with merge commit {sha} and base main")
              raise SystemExit(0)
          selected = matches[0]
          number = selected.get("number")
          if type(number) is not int or number <= 0:
              fail("empty or unreadable PR number")
          # The association endpoint returns a summary PR without merged_by.
          # Read that selected PR's full API response, binding it to the same
          # merge, base and head before using any identity or merge metadata.
          try:
              response = subprocess.run(
                  ["gh", "api", f"repos/{os.environ['REPO']}/pulls/{number}"],
                  check=True, capture_output=True, text=True)
              pr = json.loads(response.stdout, parse_constant=reject_constant)
          except (subprocess.CalledProcessError, OSError, ValueError, RecursionError):
              fail("unreadable resolved PR response")
          if (not isinstance(pr, dict) or type(pr.get("number")) is not int
                  or pr.get("number") != number
                  or pr.get("state") != "closed" or pr.get("merged") is not True
                  or pr.get("merge_commit_sha") != sha
                  or field(pr, "base", "ref") != "main"
                  or field(pr, "head", "sha") != field(selected, "head", "sha")
                  or field(pr, "merged_at") != field(selected, "merged_at")):
              fail("resolved PR no longer matches the pushed merge")
          head_sha = field(pr, "head", "sha")
          if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
              fail("empty or unreadable PR head SHA")
          head = pr.get("head")
          repo = head.get("repo") if isinstance(head, dict) else None
          fork = repo.get("fork") if isinstance(repo, dict) else None
          if type(fork) is not bool:
              fail("empty or unreadable PR field: head.repo.fork")
          values = {
              "merge_sha": sha,
              "head_sha": head_sha,
              "pr_number": number,
              "pr_url": field(pr, "html_url"),
              "merged_at": field(pr, "merged_at"),
              "head_repo": field(pr, "head", "repo", "full_name"),
              "head_is_fork": str(fork).lower(),
              "association": field(pr, "author_association"),
              "pr_author": field(pr, "user", "login"),
              "merged_by": field(pr, "merged_by", "login"),
              "ok": "true",
          }
          with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
              for key, value in values.items():
                  output.write(f"{key}={value}\n")
          PY

      - name: Only our own merged pull requests and dispatches promote the baseline
        id: authorize
        if: steps.pr.outputs.ok == 'true'
        env:
          EVENT: ${{ github.event_name }}
          REPO: ${{ github.repository }}
          HEAD_REPO: ${{ steps.pr.outputs.head_repo }}
          HEAD_IS_FORK: ${{ steps.pr.outputs.head_is_fork }}
          ASSOCIATION: ${{ steps.pr.outputs.association }}
          PR_AUTHOR: ${{ steps.pr.outputs.pr_author }}
          MERGED_BY: ${{ steps.pr.outputs.merged_by }}
          GH_TOKEN: ${{ github.token }}   # read-only use: permission lookups for the PR author and merger
          # Bot logins whose same-repository pull requests count as ours: dependabot[bot] (CI-DEPBOT A)
          # and the org's own GitHub App bot logins by name (space-separated), nothing else.
          ALLOWED_BOTS: "dependabot[bot] pineforge-app[bot]"
        run: |
          set -euo pipefail
          fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }
          case "$EVENT" in
            workflow_dispatch)
              # Only someone with write access to this repository (or our App's token) can cause these
              # events; none of them runs pull-request code.
              ;;
            push)
              [ -n "$REPO" ] || fail "repository is empty"
              [ "$HEAD_REPO" = "$REPO" ] || fail "pull request from another repository (${HEAD_REPO:-unknown})"
              [ "$HEAD_IS_FORK" = "false" ] || fail "pull request from a fork"
              [ -n "$ASSOCIATION" ] || fail "pull request author association is empty"
              [ -n "$PR_AUTHOR" ] || fail "pull request author is empty"
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
              esac
              [ -n "$MERGED_BY" ] || fail "push merger is empty"
              case " $ALLOWED_BOTS " in
                *" $MERGED_BY "*) ;;
                *)
                  # No author_association exists for the merger: ask GitHub for the merger's permission on
                  # this repository; anything but admin/maintain/write (or a failed call) refuses.
                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo none)
                  case "$perm" in admin|maintain|write) ;; *) fail "push merger ${MERGED_BY:-unknown} has permission '$perm', not write" ;; esac ;;
              esac ;;
            *) fail "event $EVENT does not run baseline promotion (only push and workflow_dispatch are allowed)" ;;
          esac
          echo "authz: allowed ($EVENT${PR_AUTHOR:+ by $PR_AUTHOR})" >> "$GITHUB_STEP_SUMMARY"
          echo "ok=true" >> "$GITHUB_OUTPUT"

'''


def assert_promotion_guard(workflow):
    assert not re.search(
        r"""^(?:defaults|env|'defaults'|'env'|"defaults"|"env")[ \t]*:[ \t]*""",
        workflow,
        re.M,
    ), "promotion must not define workflow-level defaults or env"
    trigger = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.findall(r"^  ([a-z_]+):", trigger, re.M) == [
        "push", "workflow_dispatch",
    ], "promotion accepts only push to main and manual dispatch"
    assert "  push:\n    branches: [main]\n" in trigger
    assert "pull_request_target" not in workflow
    assert "github.event.pull_request" not in workflow, "promotion must use resolved PR metadata"
    permissions = workflow.split("\npermissions:\n", 1)[1].split("\njobs:\n", 1)[0]
    assert re.findall(r"^  ([a-z-]+): ([a-z]+)$", permissions, re.M) == [
        ("contents", "read"), ("pull-requests", "read"), ("statuses", "read"),
        ("checks", "read"), ("id-token", "write"),
    ], "promotion must keep the engine permission policy"
    jobs = job_blocks(workflow)
    assert tuple(jobs) == ("promote-guard", "promote"), "promotion must start with its guard"
    guard = jobs["promote-guard"]
    assert "secrets." not in guard, "promotion guard must not receive secrets"
    assert "checkout" not in guard.lower(), "promotion guard must not check out code"
    assert "id-token" not in guard, "promotion guard must not request an OIDC token"
    assert re.findall(r"^    permissions:\n((?:      [^\n]*\n)+)", guard, re.M) == [
        "      pull-requests: read\n",
    ], (
        "promotion guard must have only pull-request read permission"
    )
    guard_block = re.search(
        r"^  promote-guard:[^\n]*\n.*?(?=^  promote:)", workflow, re.M | re.S
    )
    assert guard_block is not None and guard_block[0] == PROMOTE_GUARD, (
        "promotion guard differs from pinned policy"
    )
    promote = jobs["promote"]
    assert promote.startswith(
        "\n    needs: [promote-guard]\n"
        "    if: ${{ needs.promote-guard.result == 'success' && needs.promote-guard.outputs.ok == 'true' }}\n"
        "    runs-on: ubuntu-latest\n"
        "    environment: release-bot\n"
        "    steps:\n"
    ), "credential job must depend on the guard without a status-function bypass"
    for key in ("needs", "if", "runs-on", "steps"):
        assert len(re.findall(rf"^    {key}:", promote, re.M)) == 1, (
            f"credential job must not override {key}"
        )
    for field, count in (
        ("merge_sha", 2), ("head_sha", 3), ("pr_number", 2),
        ("pr_url", 1), ("merged_at", 1),
    ):
        key = {"merge_sha": "MERGE_SHA", "head_sha": "HEAD_SHA"}.get(field, field.upper())
        assert promote.count(
            f"          {key}: ${{{{ needs.promote-guard.outputs.{field} }}}}\n"
        ) == count, f"promotion must bind {field} to the resolved PR"
    assert "          BASE_REF: main\n" in promote
    assert (
        "      # The pushed main commit (or the dispatch ref), never the PR.\n"
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
        "        with:\n"
        "          fetch-depth: 0\n\n"
    ) in promote, "promotion checkout must keep the trusted main ref"


def test_promote_baseline_requires_trusted_head_author_and_merger():
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_promotion_guard(workflow)


def test_promotion_guard_rejects_inline_job_mapping():
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    inline_job = (
        '  bypass: {runs-on: ubuntu-latest, steps: [{run: "true", env: '
        '{APP_KEY: "${{ secrets.PINEFORGE_APP_PRIVATE_KEY }}"}}]}\n'
    )
    jobs_marker = "\njobs:\n"
    assert workflow.count(jobs_marker) == 1
    mutant = workflow.replace(jobs_marker, jobs_marker + inline_job, 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="checked block format"):
        assert_promotion_guard(mutant)


@pytest.mark.parametrize("inherited", [
    pytest.param(
        "defaults:\n"
        "  run:\n"
        '    shell: \'bash -c "bash {0}; exit 0"\'\n',
        id="workflow-default-shell",
    ),
    pytest.param(
        "env: {APP_KEY: '${{ secrets.PINEFORGE_APP_PRIVATE_KEY }}'}\n",
        id="workflow-env-secret",
    ),
])
def test_promotion_guard_rejects_workflow_level_inherited_settings(inherited):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    jobs_marker = "\njobs:\n"
    assert workflow.count(jobs_marker) == 1
    mutant = workflow.replace(jobs_marker, "\n" + inherited + "jobs:\n", 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="workflow-level"):
        assert_promotion_guard(mutant)


@dataclass(frozen=True)
class PromotionCase:
    name: str
    env: dict[str, str]
    expected_exit: int
    refusal: str = ""
    author_permission: str = "none"
    author_lookup_exit: int = 0
    author_lookup: bool = False
    merger_permission: str = "write"
    merger_lookup_exit: int = 0
    merger_lookup: bool = False


# Expected decisions are independent of the workflow's script and literal pin.
PROMOTION_CASES = [
    PromotionCase(
        "foreign-head-empty-merger",
        {"HEAD_REPO": "outsider/fork", "HEAD_IS_FORK": "true",
         "ASSOCIATION": "NONE", "MERGED_BY": ""},
        1, "pull request from another repository (outsider/fork)",
    ),
    PromotionCase(
        "fork-head-merged",
        {"HEAD_REPO": "outsider/fork", "HEAD_IS_FORK": "true",
         "ASSOCIATION": "NONE"},
        1, "pull request from another repository (outsider/fork)",
    ),
    PromotionCase(
        "same-repo-fork-flag", {"HEAD_IS_FORK": "true", "ASSOCIATION": "NONE"},
        1, "pull request from a fork",
    ),
    PromotionCase(
        "same-repo-fork-flag-empty-merger",
        {"HEAD_IS_FORK": "true", "MERGED_BY": ""},
        1, "pull request from a fork",
    ),
    PromotionCase(
        "missing-head-repository", {"HEAD_REPO": ""},
        1, "pull request from another repository (unknown)",
    ),
    PromotionCase(
        "missing-fork-flag", {"HEAD_IS_FORK": ""},
        1, "pull request from a fork",
    ),
    PromotionCase(
        "none-author-empty-merger",
        {"ASSOCIATION": "NONE", "PR_AUTHOR": "outsider", "MERGED_BY": ""},
        1, "author outsider (NONE) has permission 'none', not write, and is not an allowlisted bot",
        author_lookup=True,
    ),
    PromotionCase(
        "none-author-none",
        {"ASSOCIATION": "NONE", "PR_AUTHOR": "outsider"},
        1, "author outsider (NONE) has permission 'none', not write, and is not an allowlisted bot",
        author_permission="none", author_lookup=True,
    ),
    PromotionCase(
        "unknown-author-association",
        {"ASSOCIATION": "UNKNOWN", "PR_AUTHOR": "outsider"},
        1, "author outsider (UNKNOWN) has permission 'none', not write, and is not an allowlisted bot",
        author_lookup=True,
    ),
    PromotionCase(
        "empty-author", {"ASSOCIATION": "", "PR_AUTHOR": ""},
        1, "pull request author association is empty",
    ),
    PromotionCase(
        "empty-author-login", {"PR_AUTHOR": ""},
        1, "pull request author is empty",
    ),
    PromotionCase(
        "empty-repository", {"REPO": ""},
        1, "repository is empty",
    ),
    PromotionCase(
        "contributor-author-write-merger-write",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        0, author_permission="write", author_lookup=True, merger_lookup=True,
    ),
    PromotionCase(
        "contributor-author-admin",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        0, author_permission="admin", author_lookup=True, merger_lookup=True,
    ),
    PromotionCase(
        "contributor-author-maintain",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        0, author_permission="maintain", author_lookup=True, merger_lookup=True,
    ),
    PromotionCase(
        "contributor-author-read",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        1, "author agent (CONTRIBUTOR) has permission 'read', not write, and is not an allowlisted bot",
        author_permission="read", author_lookup=True,
    ),
    PromotionCase(
        "contributor-author-lookup-fails",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        1, "author agent (CONTRIBUTOR) has permission 'none', not write, and is not an allowlisted bot",
        author_permission="", author_lookup_exit=1, author_lookup=True,
    ),
    PromotionCase(
        "contributor-author-lookup-fails-with-write-output",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        1, "author agent (CONTRIBUTOR) has permission 'write\nnone', not write, and is not an allowlisted bot",
        author_permission="write", author_lookup_exit=1, author_lookup=True,
    ),
    PromotionCase(
        "contributor-author-permission-empty",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        1, "author agent (CONTRIBUTOR) has permission '', not write, and is not an allowlisted bot",
        author_permission="", author_lookup=True,
    ),
    PromotionCase(
        "contributor-author-write-merger-read",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent"},
        1, "push merger writer has permission 'read', not write",
        author_permission="write", author_lookup=True,
        merger_permission="read", merger_lookup=True,
    ),
    PromotionCase(
        "contributor-empty-merger",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent",
         "MERGED_BY": ""},
        1, "push merger is empty", author_permission="write", author_lookup=True,
    ),
    PromotionCase("member-merged-by-write-user", {}, 0, merger_lookup=True),
    PromotionCase(
        "owner-merged-by-admin", {"ASSOCIATION": "OWNER"},
        0, merger_permission="admin", merger_lookup=True,
    ),
    PromotionCase(
        "collaborator-merged-by-maintainer", {"ASSOCIATION": "COLLABORATOR"},
        0, merger_permission="maintain", merger_lookup=True,
    ),
    PromotionCase(
        "merger-read-permission", {},
        1, "push merger writer has permission 'read', not write",
        merger_permission="read", merger_lookup=True,
    ),
    PromotionCase(
        "merger-triage-permission", {},
        1, "push merger writer has permission 'triage', not write",
        merger_permission="triage", merger_lookup=True,
    ),
    PromotionCase(
        "merger-lookup-fails", {},
        1, "push merger writer has permission 'none', not write",
        merger_permission="", merger_lookup_exit=1, merger_lookup=True,
    ),
    PromotionCase(
        "merger-lookup-fails-with-write-output", {},
        1, "push merger writer has permission 'write\nnone', not write",
        merger_permission="write", merger_lookup_exit=1, merger_lookup=True,
    ),
    PromotionCase(
        "merger-permission-empty", {},
        1, "push merger writer has permission '', not write",
        merger_permission="", merger_lookup=True,
    ),
    PromotionCase(
        "empty-merger", {"MERGED_BY": ""},
        1, "push merger is empty",
    ),
    PromotionCase(
        "member-empty-merger", {"MERGED_BY": ""}, 1, "push merger is empty",
    ),
    PromotionCase(
        "dependabot-author-write-merger",
        {"ASSOCIATION": "NONE", "PR_AUTHOR": "dependabot[bot]"}, 0, merger_lookup=True,
    ),
    PromotionCase(
        "app-bot-author-write-merger",
        {"ASSOCIATION": "NONE", "PR_AUTHOR": "pineforge-app[bot]"}, 0, merger_lookup=True,
    ),
    PromotionCase(
        "dependabot-merger", {"MERGED_BY": "dependabot[bot]"},
        0, merger_permission="", merger_lookup_exit=1,
    ),
    PromotionCase(
        "app-bot-merger", {"MERGED_BY": "pineforge-app[bot]"},
        0, merger_permission="", merger_lookup_exit=1,
    ),
    PromotionCase(
        "manual-dispatch",
        {"EVENT": "workflow_dispatch", "HEAD_REPO": "", "HEAD_IS_FORK": "",
         "ASSOCIATION": "", "PR_AUTHOR": "", "MERGED_BY": ""},
        0,
    ),
    PromotionCase(
        "push", {"EVENT": "push"},
        0, merger_lookup=True,
    ),
    PromotionCase(
        "target-event", {"EVENT": "pull_request_target"},
        1, "event pull_request_target does not run baseline promotion (only push and workflow_dispatch are allowed)",
    ),
    PromotionCase(
        "workflow-run", {"EVENT": "workflow_run"},
        1, "event workflow_run does not run baseline promotion (only push and workflow_dispatch are allowed)",
    ),
    PromotionCase(
        "unknown-event", {"EVENT": "unexpected"},
        1, "event unexpected does not run baseline promotion (only push and workflow_dispatch are allowed)",
    ),
]


def promotion_guard_script(workflow):
    """Extract the real authorization script, after resolution on the new route."""
    guard = job_blocks(workflow)["promote-guard"]
    scripts = re.findall(r"^        run: \|\n((?:          [^\n]*\n|\n)+)", guard, re.M)
    assert scripts, "promotion guard must have an authorization script"
    return textwrap.dedent(scripts[-1])


def assert_promotion_guard_case(workflow, case, tmp_path):
    # Do not call the literal pin here: mutations must reach bash themselves.
    script = promotion_guard_script(workflow)
    bash = shutil.which("bash")
    if bash is None:
        pytest.fail("NOT RUN: promotion guard decision table requires bash on the test runner")
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{bash}\n"
        'printf "%s\\n" "$*" >> "$GH_CALLS"\n'
        '[ "$#" -eq 4 ] && [ "$1" = api ] && '
        '[ "$3" = --jq ] && '
        '[ "$4" = .permission ] || exit 97\n'
        'case "$2" in\n'
        '  "repos/$REPO/collaborators/$PR_AUTHOR/permission")\n'
        '    if [ -n "$AUTHOR_PERMISSION" ]; then printf "%s\\n" "$AUTHOR_PERMISSION"; fi\n'
        '    exit "$AUTHOR_LOOKUP_EXIT" ;;\n'
        '  "repos/$REPO/collaborators/$MERGED_BY/permission")\n'
        '    if [ -n "$MERGER_PERMISSION" ]; then printf "%s\\n" "$MERGER_PERMISSION"; fi\n'
        '    exit "$MERGER_LOOKUP_EXIT" ;;\n'
        '  *) exit 97 ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    gh.chmod(0o700)
    summary_path = tmp_path / "summary"
    calls_path = tmp_path / "gh-calls"
    output_path = tmp_path / "output"
    env = {
        "EVENT": "push",
        "REPO": "pineforge-4pass/pineforge-codegen-oss",
        "HEAD_REPO": "pineforge-4pass/pineforge-codegen-oss",
        "HEAD_IS_FORK": "false",
        "ASSOCIATION": "MEMBER", "MERGED": "true",
        "PR_AUTHOR": "member",
        "MERGED_BY": "writer",
        "GH_TOKEN": "",
        "ALLOWED_BOTS": "dependabot[bot] pineforge-app[bot]",
        **case.env,
    }
    env.update({
        # Only the gh CLI is stubbed; bash runs the workflow's actual script.
        # No inherited tokens, shell startup files or real gh can be reached.
        "PATH": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
        "GITHUB_OUTPUT": str(output_path),
        "GH_CALLS": str(calls_path),
        "AUTHOR_PERMISSION": case.author_permission,
        "AUTHOR_LOOKUP_EXIT": str(case.author_lookup_exit),
        "MERGER_PERMISSION": case.merger_permission,
        "MERGER_LOOKUP_EXIT": str(case.merger_lookup_exit),
    })
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == case.expected_exit, (
        f"{case.name}: expected exit {case.expected_exit}, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stderr == "", f"{case.name}: unexpected shell error: {result.stderr}"
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    if case.expected_exit == 1:
        assert result.stdout == f"::error title=authz::{case.refusal}\n", case.name
        assert summary == f"authz: REFUSED: {case.refusal}\n", case.name
    else:
        author = f" by {env['PR_AUTHOR']}" if env["PR_AUTHOR"] else ""
        assert summary == f"authz: allowed ({env['EVENT']}{author})\n", case.name
        assert result.stdout == "", case.name
    output = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    assert output == ("ok=true\n" if case.expected_exit == 0 else ""), case.name
    calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
    expected_calls = []
    if case.author_lookup:
        expected_calls.append(
            f"api repos/{env['REPO']}/collaborators/{env['PR_AUTHOR']}/permission --jq .permission"
        )
    if case.merger_lookup:
        expected_calls.append(
            f"api repos/{env['REPO']}/collaborators/{env['MERGED_BY']}/permission --jq .permission"
        )
    assert calls == expected_calls, f"{case.name}: unexpected permission lookups: {calls}"


@pytest.mark.parametrize("case", PROMOTION_CASES, ids=[case.name for case in PROMOTION_CASES])
def test_promotion_guard_executes_workflow_decision_table(case, tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_promotion_guard_case(workflow, case, tmp_path)


def test_promotion_guard_rejects_script_replaced_by_exit_zero(tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    script = textwrap.indent(promotion_guard_script(workflow), " " * 10)
    assert workflow.count(script) == 1, "mutation must find the real guard script exactly once"
    mutant = workflow.replace(script, "          exit 0\n", 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="promotion guard differs"):
        assert_promotion_guard(mutant)
    case = next(case for case in PROMOTION_CASES if case.name == "foreign-head-empty-merger")
    with pytest.raises(AssertionError, match=case.name):
        assert_promotion_guard_case(mutant, case, tmp_path)


@pytest.mark.parametrize("original,replacement,case_name", [
    pytest.param(
        '          fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }\n          case "$EVENT" in\n',
        '          fail() { :; }\n          case "$EVENT" in\n',
        "foreign-head-empty-merger", id="no-op-fail",
    ),
    pytest.param(
        '              [ "$HEAD_IS_FORK" = "false" ] || fail "pull request from a fork"\n',
        "", "same-repo-fork-flag", id="deleted-fork-refusal",
    ),
    pytest.param(
        '                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo none)\n',
        "", "contributor-author-write-merger-write", id="deleted-author-lookup",
    ),
    pytest.param(
        '                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo none)\n',
        '                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo write)\n',
        "contributor-author-lookup-fails", id="failed-author-lookup-accepted",
    ),
    pytest.param(
        '                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo none)\n',
        "", "contributor-author-write-merger-read", id="deleted-merger-lookup",
    ),
    pytest.param(
        '                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo none)\n',
        '                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo write)\n',
        "merger-lookup-fails", id="failed-merger-lookup-accepted",
    ),
    pytest.param(
        '              [ -n "$MERGED_BY" ] || fail "push merger is empty"\n',
        "", "empty-merger", id="empty-merger-accepted",
    ),
])
def test_decision_table_rejects_guard_script_mutations(original, replacement, case_name, tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert workflow.count(original) == 1, "mutation must find the real workflow exactly once"
    mutant = workflow.replace(original, replacement, 1)
    assert mutant != workflow
    with pytest.raises(AssertionError, match="promotion guard differs"):
        assert_promotion_guard(mutant)
    case = next(case for case in PROMOTION_CASES if case.name == case_name)
    with pytest.raises(AssertionError, match=case.name):
        assert_promotion_guard_case(mutant, case, tmp_path)


@pytest.mark.parametrize("name", [
    "EVENT", "REPO", "HEAD_REPO", "HEAD_IS_FORK", "ASSOCIATION", "PR_AUTHOR",
    "MERGED_BY", "GH_TOKEN", "ALLOWED_BOTS", "EVENT_NAME", "REF", "SHA",
    "INPUT_MERGE_SHA", "INPUT_HEAD_SHA", "INPUT_PR_NUMBER", "INPUT_PR_URL", "INPUT_MERGED_AT",
])
def test_promotion_guard_rejects_missing_event_and_identity_fields(name):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    # The first occurrence belongs to the first job, not a later credential step.
    mutant, count = re.subn(
        rf"^          {name}: [^\n]+\n", "", workflow, count=1, flags=re.M
    )
    assert count == 1, f"mutation must remove the actual guard's {name} field"
    with pytest.raises(AssertionError, match="promotion guard differs"):
        assert_promotion_guard(mutant)


@pytest.mark.parametrize("original,replacement", [
    pytest.param(
        "  promote-guard:\n",
        "  promote-guard: # changed header\n",
        id="guard-header-changed",
    ),
    pytest.param(
        "      - name: Only our own merged pull requests and dispatches promote the baseline\n",
        "      - name: Promote the baseline\n",
        id="guard-step-text-changed",
    ),
    pytest.param(
        "          # Bot logins whose same-repository pull requests count as ours: dependabot[bot] (CI-DEPBOT A)\n",
        "",
        id="guard-bot-comment-removed",
    ),
    pytest.param(
        '          echo "ok=true" >> "$GITHUB_OUTPUT"\n\n  promote:\n',
        '          echo "ok=true" >> "$GITHUB_OUTPUT"\n  promote:\n',
        id="guard-separator-removed",
    ),
    pytest.param(
        '          fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }\n          case "$EVENT" in\n',
        '          fail() { :; }\n          case "$EVENT" in\n',
        id="fail-is-no-op",
    ),
    pytest.param(
        '              [ "$HEAD_REPO" = "$REPO" ] || fail "pull request from another repository (${HEAD_REPO:-unknown})"\n',
        "",
        id="foreign-head-accepted",
    ),
    pytest.param(
        '              [ "$HEAD_IS_FORK" = "false" ] || fail "pull request from a fork"\n',
        "",
        id="fork-head-accepted",
    ),
    pytest.param(
        "OWNER|MEMBER|COLLABORATOR) ;;",
        "OWNER|MEMBER|COLLABORATOR|NONE) ;;",
        id="untrusted-author-accepted",
    ),
    pytest.param(
        'ALLOWED_BOTS: "dependabot[bot] pineforge-app[bot]"',
        'ALLOWED_BOTS: "dependabot[bot] pineforge-app[bot] stranger[bot]"',
        id="bot-allowlist-widened",
    ),
    pytest.param(
        '                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo none)\n',
        "",
        id="author-permission-lookup-removed",
    ),
    pytest.param(
        '                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo none)\n',
        '                      perm=$(gh api "repos/$REPO/collaborators/$PR_AUTHOR/permission" --jq .permission 2>/dev/null || echo write)\n',
        id="failed-author-lookup-accepted",
    ),
    pytest.param(
        '''case "$perm" in admin|maintain|write) ;; *) fail "author''',
        '''case "$perm" in admin|maintain|write|read|none) ;; *) fail "author''',
        id="non-write-author-accepted",
    ),
    pytest.param(
        '                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo none)\n',
        "",
        id="merger-permission-lookup-removed",
    ),
    pytest.param(
        '                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo none)\n',
        '                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo write)\n',
        id="failed-merger-lookup-accepted",
    ),
    pytest.param(
        '''case "$perm" in admin|maintain|write) ;; *) fail "push merger''',
        '''case "$perm" in admin|maintain|write|read|none) ;; *) fail "push merger''',
        id="non-write-merger-accepted",
    ),
    pytest.param(
        "        if: steps.pr.outputs.ok == 'true'\n",
        "        if: steps.pr.outputs.ok != 'false'\n",
        id="unresolved-pr-accepted",
    ),
    pytest.param(
        '            *) fail "event $EVENT does not run baseline promotion (only push and workflow_dispatch are allowed)" ;;\n',
        "            *) ;;\n",
        id="unknown-event-accepted",
    ),
    pytest.param(
        '          case "$EVENT" in\n            workflow_dispatch)\n',
        '          case "$EVENT" in\n            pull_request|workflow_dispatch)\n',
        id="extra-event-accepted",
    ),
    pytest.param(
        "    permissions:\n      pull-requests: read\n",
        "",
        id="guard-inherits-oidc-permissions",
    ),
    pytest.param(
        "    permissions:\n      pull-requests: read\n",
        "    permissions:\n      id-token: write\n",
        id="guard-requests-oidc-token",
    ),
    pytest.param(
        '              [ -n "$MERGED_BY" ] || fail "push merger is empty"\n',
        "",
        id="empty-merger-accepted",
    ),
    pytest.param(
        "          EVENT: ${{ github.event_name }}\n",
        "          SECRET: ${{ secrets.PINEFORGE_APP_PRIVATE_KEY }}\n"
        "          EVENT: ${{ github.event_name }}\n",
        id="guard-receives-secret",
    ),
    pytest.param(
        "      - name: Only our own merged pull requests and dispatches promote the baseline\n",
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
        "      - name: Only our own merged pull requests and dispatches promote the baseline\n",
        id="guard-checks-out-code",
    ),
    pytest.param(
        "    needs: [promote-guard]\n",
        "",
        id="credential-job-detached",
    ),
    pytest.param(
        "    if: ${{ needs.promote-guard.result == 'success' && needs.promote-guard.outputs.ok == 'true' }}\n",
        "    if: ${{ always() && needs.promote-guard.outputs.ok == 'true' }}\n",
        id="credential-job-bypasses-failed-guard",
    ),
    pytest.param(
        "    runs-on: ubuntu-latest\n    environment: release-bot\n    steps:\n",
        "    runs-on: unreviewed-runner\n    environment: release-bot\n    steps:\n",
        id="credential-runner-changed",
    ),
    pytest.param(
        "    steps:\n      - name: Determine repo axis\n",
        "    needs: []\n    steps:\n      - name: Determine repo axis\n",
        id="dependency-overridden",
    ),
    pytest.param(
        "\njobs:\n  promote-guard:\n",
        "\njobs:\n  renamed-guard:\n",
        id="first-guard-missing",
    ),
    pytest.param(
        "  push:\n    branches: [main]\n",
        "  push:\n    branches: [release]\n",
        id="trigger-no-longer-main",
    ),
    pytest.param(
        "  workflow_dispatch:\n    inputs:\n",
        "  workflow_call:\n    inputs:\n",
        id="dispatch-trigger-replaced",
    ),
    pytest.param(
        "\npermissions:\n  contents: read\n",
        "\npermissions:\n  contents: write\n",
        id="promotion-permissions-widened",
    ),
])
def test_promotion_guard_rejects_workflow_mutations(original, replacement):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert workflow.count(original) == 1, "mutation must match the actual workflow exactly once"
    mutant = workflow.replace(original, replacement, 1)
    assert mutant != workflow, "mutation must change the actual workflow"
    with pytest.raises(AssertionError):
        assert_promotion_guard(mutant)


@pytest.mark.parametrize("override", [
    "needs: []",
    "if: ${{ always() }}",
    "runs-on: unreviewed-runner",
    "steps: []",
])
def test_promotion_guard_rejects_late_credential_job_overrides(override):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    # Preserve the checked header and append a duplicate key to the real job.
    mutant = workflow + f"\n    {override}\n"
    with pytest.raises(AssertionError, match="credential job must not override"):
        assert_promotion_guard(mutant)


def assert_test_workflow_is_canonical(test_workflow, gate_workflow):
    """The push/dispatch workflow retains its canonical text."""
    assert_test_workflow_has_canonical_trigger(test_workflow)
    assert_test_jobs_keep_canonical_conditions(test_workflow)
    jobs = job_blocks(test_workflow)
    lint = jobs["workflow-lint"]
    assert not re.search(r"^    if:", lint, re.M), (
        "the canonical workflow lint must remain unconditional after authz"
    )
    assert 'ref: "${{ github.event.pull_request.head.sha || github.sha }}"' in lint, (
        "workflow lint must check out the exact head"
    )
    assert "if [[ -f .github/actionlint.yaml ]]; then" not in lint
    assert "lint_config=" not in lint
    assert '-config-file .github/actionlint.yaml' in lint
    trigger = gate_workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "pull_request" not in trigger, "the old gate must have no PR trigger"
    assert ".candidate" not in gate_workflow, "the old gate must not check out PR code"


def test_test_workflow_keeps_canonical_jobs_and_gate_stays_untriggered():
    assert_test_workflow_is_canonical(
        (WORKFLOWS / "test.yml").read_text(encoding="utf-8"),
        (WORKFLOWS / "gate.yml").read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("name", ["changes", "package-files"])
def test_canonical_light_job_pr_guard_mutations_are_rejected(name):
    test_workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    gate_workflow = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    body = job_blocks(test_workflow)[name]
    original = "    needs: [authz]\n"
    assert body.count(original) == 1
    changed_body = body.replace(
        original,
        original + "    if: github.event_name != 'pull_request'\n",
        1,
    )
    mutant = test_workflow.replace(body, changed_body, 1)
    with pytest.raises(AssertionError, match=f"{name}: canonical job"):
        assert_test_workflow_is_canonical(mutant, gate_workflow)


def assert_pod_head_checkout(body):
    assert "    needs: [admit]" in body, "PR payload must depend on admission"
    assert "needs.admit.outputs.allowed == 'true'" in body, "PR payload must require allowed"
    assert re.search(r"^    runs-on: gke-8cpu-16gb-amd64$", body, re.M), (
        "PR payload must use the bare GKE scalar"
    )
    assert "    permissions: {contents: read}" in body
    assert "          repository: ${{ github.repository }}" in body
    assert "          ref: ${{ needs.admit.outputs.head_sha }}" in body, (
        "PR checkout must use the immutable admission output"
    )
    assert "          persist-credentials: false" in body
    assert "secrets." not in body
    assert "id-token:" not in body
    assert "environment:" not in body
    assert "actions/cache" not in body
    assert not re.search(r"^\s+cache:", body, re.M)


def test_pr_payloads_use_only_admitted_bare_scalar_pods():
    # Inspect the old PR gate on main so the regression is an admission
    # failure, rather than a missing-file error.
    path = WORKFLOWS / "gke-pr.yml"
    workflow = (path if path.exists() else WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    jobs = job_blocks(workflow)
    for name in ("differential", "pytest", "workflow-lint", "package-files"):
        assert_pod_head_checkout(jobs[name])


@pytest.mark.parametrize("before,after,message", [
    ("needs: [admit]", "needs: []", "depend on admission"),
    ("outputs.allowed == 'true'", "outputs.allowed != 'false'", "require allowed"),
    ("runs-on: gke-8cpu-16gb-amd64", "runs-on: [self-hosted, gke-8cpu-16gb-amd64]", "bare GKE"),
    ("ref: ${{ needs.admit.outputs.head_sha }}", "ref: ${{ github.event.pull_request.head.sha }}", "immutable admission"),
    ("persist-credentials: false", "persist-credentials: true", "persist-credentials"),
])
def test_pod_checkout_mutations_are_rejected(before, after, message):
    body = job_blocks((WORKFLOWS / "gke-pr.yml").read_text(encoding="utf-8"))["pytest"]
    assert body.count(before) == 1
    with pytest.raises(AssertionError, match=message):
        assert_pod_head_checkout(body.replace(before, after, 1))


def test_pr_node_setup_explicitly_disables_automatic_cache():
    path = WORKFLOWS / "gke-pr.yml"
    workflow = (path if path.exists() else WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    steps = re.split(r"(?=^      - )", job_blocks(workflow)["differential"], flags=re.M)
    node_steps = [step for step in steps if "uses: actions/setup-node@" in step]
    assert len(node_steps) == 1
    assert re.search(r"^          package-manager-cache: false$", node_steps[0], re.M), (
        "PR Node setup must explicitly disable automatic Actions caching"
    )


def test_workflow_lint_checks_the_exact_head_with_repository_config():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    body = job_blocks(workflow)["workflow-lint"]
    assert not re.search(r"^    if:", body, re.M)
    assert 'ref: "${{ github.event.pull_request.head.sha || github.sha }}"' in body, (
        "workflow lint must check out the exact head"
    )
    assert '        if: "${{ needs.authz.result == \'success\' }}"' in body
    assert "python3 scripts/check_workflow_app_environments.py" in body
    assert "if [[ -f .github/actionlint.yaml ]]; then" not in body
    assert "lint_config=" not in body
    assert "-config-file .github/actionlint.yaml" in body


@pytest.mark.parametrize("identity", ["PR_AUTHOR", "ACTOR", "TRIGGERING_ACTOR"])
def test_pr_admission_binds_each_membership_identity(identity):
    path = WORKFLOWS / "gke-pr.yml"
    workflow = (path if path.exists() else WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    blocks = job_blocks(workflow)
    # The old entry is real workflow text, so main fails for its absent
    # permission boundary, rather than for a missing new helper or file.
    boundary = blocks.get("admit", blocks.get("preflight-docs", ""))
    assert re.search(rf"^          {identity}: \$\{{\{{ github\.", boundary, re.M), (
        f"{identity}: privileged PR entry must bind the member identity"
    )
    assert "run: python3 -I tests/test_gke_pr_admission.py --identity" in boundary


def test_privileged_hosted_entry_never_checks_out_the_pr_head():
    path = WORKFLOWS / "gke-pr.yml"
    workflow = (path if path.exists() else WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    blocks = job_blocks(workflow)
    boundary = blocks.get("admit", blocks.get("preflight-docs", ""))
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in boundary, (
        "privileged hosted entry must never check out candidate code"
    )
    assert "ref: ${{ github.sha }}" in boundary


def target_workflow_files(sources):
    targets = []
    for name, workflow in sources.items():
        trigger = re.search(
            r"""^(?:on|'on'|"on"):[^\n]*(?:\n(?:[ \t]+[^\n]*|))*""",
            workflow, re.M,
        )
        assert trigger is not None, f"{name}: missing event declaration"
        if re.search(r"\bpull_request_target\b", trigger[0]):
            targets.append(name)
    return sorted(targets)


def assert_only_admission_uses_target_events(sources):
    assert target_workflow_files(sources) == ["gke-pr.yml"], (
        "only admission may use a target trigger"
    )


def test_only_admission_uses_target_events():
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.iterdir()) if path.suffix in {".yml", ".yaml"}
    }
    assert_only_admission_uses_target_events(sources)
    mutant = {**sources, "extra.yml": "on: [push, pull_request_target]\njobs:\n"}
    assert target_workflow_files(mutant) == ["extra.yml", "gke-pr.yml"]
    assert target_workflow_files(mutant) != target_workflow_files(sources)
    with pytest.raises(AssertionError, match="only admission"):
        assert_only_admission_uses_target_events(mutant)
    promotion = sources["promote-baseline.yml"]
    assert promotion.count("on:\n") == 1
    restored = promotion.replace(
        "on:\n", "on:\n  pull_request_target:\n    types: [closed]\n", 1
    )
    assert restored != promotion
    with pytest.raises(AssertionError, match="only admission"):
        assert_only_admission_uses_target_events({
            **sources, "promote-baseline.yml": restored,
        })


@pytest.mark.parametrize(("name", "original", "replacement"), [
    (
        "pytest",
        "    if: needs.changes.outputs.code == 'true'\n",
        "    if: github.event_name != 'pull_request'\n",
    ),
    (
        "test-check",
        "    if: ${{ always() }}\n",
        "    if: ${{ always() && github.event_name != 'pull_request' }}\n",
    ),
])
def test_canonical_aggregate_and_matrix_pr_guard_mutations_are_rejected(
    name, original, replacement,
):
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    gate = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    body = job_blocks(workflow)[name]
    assert body.count(original) == 1
    mutant = workflow.replace(body, body.replace(original, replacement, 1), 1)
    with pytest.raises(AssertionError, match=name):
        assert_test_workflow_is_canonical(mutant, gate)


def test_old_gate_pr_trigger_mutation_is_rejected():
    workflow = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    gate = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    assert gate.count("on:\n") == 1
    mutant = gate.replace("on:\n", "on:\n  pull_request_target:\n", 1)
    with pytest.raises(AssertionError, match="old gate must have no PR trigger"):
        assert_test_workflow_is_canonical(workflow, mutant)


def promotion_pr(number=17, *, merge_sha="a" * 40, base="main",
                 state="closed", merged_at="2026-10-01T00:00:00Z"):
    return {
        "number": number, "state": state, "merged": True,
        "merge_commit_sha": merge_sha, "merged_at": merged_at,
        "base": {"ref": base},
        "head": {"sha": "b" * 40, "repo": {"full_name": "owner/repo", "fork": False}},
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "author_association": "MEMBER", "user": {"login": "member"},
        "merged_by": {"login": "writer"},
    }


@dataclass(frozen=True)
class DispatchRejectCase:
    name: str
    changes: dict[str, str]
    refusal: str
    expected_calls: tuple[str, ...] = ()


def dispatch_overrides(pr=None):
    pr = promotion_pr() if pr is None else pr
    return {
        "EVENT": "workflow_dispatch",
        "EVENT_NAME": "workflow_dispatch",
        "INPUT_MERGE_SHA": pr["merge_commit_sha"],
        "INPUT_HEAD_SHA": pr["head"]["sha"],
        "INPUT_PR_NUMBER": str(pr["number"]),
        "INPUT_PR_URL": pr["html_url"],
        "INPUT_MERGED_AT": pr["merged_at"],
        "GH_DISPATCH_PR": json.dumps(pr),
        "GH_DISPATCH_PULLS": json.dumps([[pr]]),
    }


def dispatch_pr_with_head(sha):
    pr = promotion_pr()
    pr["head"] = {**pr["head"], "sha": sha}
    return json.dumps(pr)


def dispatch_pr_with_base(base):
    pr = promotion_pr()
    pr["base"] = {"ref": base}
    return json.dumps(pr)


DISPATCH_PR_CALL = "api repos/owner/repo/pulls/17"
DISPATCH_PULLS_CALL = (
    "api --paginate --slurp "
    "repos/owner/repo/commits/" + "a" * 40 + "/pulls?per_page=100"
)


DISPATCH_REJECT_CASES = [
    DispatchRejectCase(
        "merge-commit-39-hex", {"INPUT_MERGE_SHA": "a" * 39},
        "merge_commit must be exactly 40 lowercase hexadecimal characters",
    ),
    DispatchRejectCase(
        "merge-commit-41-hex", {"INPUT_MERGE_SHA": "a" * 41},
        "merge_commit must be exactly 40 lowercase hexadecimal characters",
    ),
    DispatchRejectCase(
        "merge-commit-uppercase-hex", {"INPUT_MERGE_SHA": "A" * 40},
        "merge_commit must be exactly 40 lowercase hexadecimal characters",
    ),
    DispatchRejectCase(
        "head-sha-39-hex", {"INPUT_HEAD_SHA": "b" * 39},
        "head_sha must be exactly 40 lowercase hexadecimal characters",
    ),
    DispatchRejectCase(
        "head-sha-41-hex", {"INPUT_HEAD_SHA": "b" * 41},
        "head_sha must be exactly 40 lowercase hexadecimal characters",
    ),
    DispatchRejectCase(
        "head-sha-uppercase-hex", {"INPUT_HEAD_SHA": "B" * 40},
        "head_sha must be exactly 40 lowercase hexadecimal characters",
    ),
    DispatchRejectCase(
        "pr-number-zero", {"INPUT_PR_NUMBER": "0"},
        "pr_number must be a positive decimal integer",
    ),
    DispatchRejectCase(
        "pr-number-leading-zero", {"INPUT_PR_NUMBER": "01"},
        "pr_number must be a positive decimal integer",
    ),
    DispatchRejectCase(
        "pr-number-negative", {"INPUT_PR_NUMBER": "-3"},
        "pr_number must be a positive decimal integer",
    ),
    DispatchRejectCase(
        "pr-number-nonnumeric", {"INPUT_PR_NUMBER": "abc"},
        "pr_number must be a positive decimal integer",
    ),
    DispatchRejectCase(
        "pr-url-another-repository",
        {"INPUT_PR_URL": "https://github.com/other/repo/pull/17"},
        "pr_url must equal https://github.com/owner/repo/pull/17",
    ),
    DispatchRejectCase(
        "pr-url-another-number",
        {"INPUT_PR_URL": "https://github.com/owner/repo/pull/18"},
        "pr_url must equal https://github.com/owner/repo/pull/17",
    ),
    DispatchRejectCase(
        "merged-at-wrong-form",
        {"INPUT_MERGED_AT": "2026-10-01T00:00:00.000Z"},
        "merged_at must be an ISO-8601 UTC timestamp in YYYY-MM-DDTHH:MM:SSZ form",
    ),
    DispatchRejectCase(
        "api-pr-not-merged",
        {"GH_DISPATCH_PR": json.dumps({**promotion_pr(), "merged": False})},
        "pr_number API record is not merged",
        (DISPATCH_PR_CALL,),
    ),
    DispatchRejectCase(
        "api-merge-commit-mismatch",
        {"GH_DISPATCH_PR": json.dumps({**promotion_pr(), "merge_commit_sha": "c" * 40})},
        "merge_commit does not match the PR merge_commit_sha",
        (DISPATCH_PR_CALL,),
    ),
    DispatchRejectCase(
        "api-head-mismatch",
        {"GH_DISPATCH_PR": dispatch_pr_with_head("c" * 40)},
        "head_sha does not match the PR head.sha",
        (DISPATCH_PR_CALL,),
    ),
    DispatchRejectCase(
        "api-base-not-main",
        {"GH_DISPATCH_PR": dispatch_pr_with_base("release")},
        "pr_number API record does not use base main",
        (DISPATCH_PR_CALL,),
    ),
    DispatchRejectCase(
        "api-merged-at-mismatch",
        {
            "GH_DISPATCH_PR": json.dumps({
                **promotion_pr(), "merged_at": "2026-10-02T00:00:00Z",
            }),
        },
        "merged_at does not match the PR merged_at",
        (DISPATCH_PR_CALL,),
    ),
    DispatchRejectCase(
        "api-association-has-two-prs",
        {"GH_DISPATCH_PULLS": json.dumps([[promotion_pr(), promotion_pr(18)]])},
        "merge_commit must list exactly pull request 17",
        (DISPATCH_PR_CALL, DISPATCH_PULLS_CALL),
    ),
    DispatchRejectCase(
        "api-empty-pr-response",
        {"GH_DISPATCH_PR": ""},
        "pr_number API lookup returned an empty response",
        (DISPATCH_PR_CALL,),
    ),
    DispatchRejectCase(
        "api-malformed-pr-response",
        {"GH_DISPATCH_PR": "{malformed"},
        "pr_number does not match the PR API response",
        (DISPATCH_PR_CALL,),
    ),
    DispatchRejectCase(
        "api-malformed-association-response",
        {"GH_DISPATCH_PULLS": "{malformed"},
        "merge_commit must list exactly pull request 17",
        (DISPATCH_PR_CALL, DISPATCH_PULLS_CALL),
    ),
    DispatchRejectCase(
        "api-gh-error",
        {"GH_DISPATCH_PR_EXIT": "1"},
        "pr_number API lookup failed",
        (DISPATCH_PR_CALL,),
    ),
]


def run_promotion_entry(workflow, pages, tmp_path, *, full_pr=None, overrides=None):
    """Run the shipped first guard step, stubbing only GitHub API responses.

    On the old workflow this executes its real authorization script: a push
    fails for its unsupported event, rather than for a missing resolver fixture.
    """
    scripts = workflow_run_scripts(workflow, "promote-guard")
    assert scripts, "promotion must have an entry script"
    bash = shutil.which("bash")
    assert bash, "promotion controls require bash on the test runner"
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "python3").symlink_to(sys.executable)
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{bash}\n"
        "set -eu\n"
        'printf "%s\\n" "$*" >> "$GH_CALLS"\n'
        'if [ "$EVENT_NAME" = workflow_dispatch ]; then\n'
        '  if [ "$#" -eq 2 ] && [ "$1" = api ] && '
        '[ "$2" = "repos/$REPO/pulls/$INPUT_PR_NUMBER" ]; then\n'
        '    [ "$GH_DISPATCH_PR_EXIT" -eq 0 ] || exit "$GH_DISPATCH_PR_EXIT"\n'
        '    printf "%s\\n" "$GH_DISPATCH_PR"\n'
        '  elif [ "$#" -eq 4 ] && [ "$1" = api ] && [ "$2" = --paginate ] && '
        '[ "$3" = --slurp ] && '
        '[ "$4" = "repos/$REPO/commits/$INPUT_MERGE_SHA/pulls?per_page=100" ]; then\n'
        '    [ "$GH_DISPATCH_PULLS_EXIT" -eq 0 ] || exit "$GH_DISPATCH_PULLS_EXIT"\n'
        '    printf "%s\\n" "$GH_DISPATCH_PULLS"\n'
        "  else exit 97; fi\n"
        'elif [ "$#" -eq 4 ] && [ "$1" = api ] && [ "$2" = --paginate ] && '
        '[ "$3" = --slurp ] && [ "$4" = "repos/$REPO/commits/$SHA/pulls?per_page=100" ]; then\n'
        '  printf "%s\\n" "$GH_PULLS"\n'
        'elif [ "$#" -eq 2 ] && [ "$1" = api ]; then\n'
        '  case "$2" in "repos/$REPO/pulls/"*) printf "%s\\n" "$GH_PR" ;; *) exit 97 ;; esac\n'
        "else exit 97; fi\n",
        encoding="utf-8",
    )
    gh.chmod(0o700)
    script = tmp_path / "entry.bash"
    script.write_text(scripts[0], encoding="utf-8")
    output_path = tmp_path / "output"
    summary_path = tmp_path / "summary"
    calls_path = tmp_path / "calls"
    env = {
        "PATH": str(tmp_path),
        "EVENT": "push", "EVENT_NAME": "push", "REF": "refs/heads/main",
        "REPO": "owner/repo", "SHA": "a" * 40, "GH_TOKEN": "",
        "HEAD_REPO": "owner/repo", "HEAD_IS_FORK": "false",
        "ASSOCIATION": "MEMBER", "PR_AUTHOR": "member", "MERGED": "true",
        "MERGED_BY": "writer", "ALLOWED_BOTS": "dependabot[bot] pineforge-app[bot]",
        "GITHUB_OUTPUT": str(output_path), "GITHUB_STEP_SUMMARY": str(summary_path),
        "GH_CALLS": str(calls_path), "GH_PULLS": json.dumps(pages),
        "GH_PR": json.dumps(promotion_pr() if full_pr is None else full_pr),
        "INPUT_MERGE_SHA": "", "INPUT_HEAD_SHA": "", "INPUT_PR_NUMBER": "",
        "INPUT_PR_URL": "", "INPUT_MERGED_AT": "",
        "GH_DISPATCH_PR": json.dumps(promotion_pr()),
        "GH_DISPATCH_PULLS": json.dumps([[promotion_pr()]]),
        "GH_DISPATCH_PR_EXIT": "0", "GH_DISPATCH_PULLS_EXIT": "0",
        **(overrides or {}),
    }
    result = subprocess.run(
        [bash, "--noprofile", "--norc", str(script)],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    rows = output_path.read_text(encoding="utf-8").splitlines() if output_path.exists() else []
    outputs = dict(row.split("=", 1) for row in rows)
    assert len(rows) == len(outputs), "promotion must not overwrite an output"
    calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    return result, outputs, calls, summary


def assert_promotion_resolution(workflow, pages, tmp_path, expected_count):
    result, outputs, calls, summary = run_promotion_entry(workflow, pages, tmp_path)
    assert result.returncode == 0, (
        f"push resolution must exit green; got {result.returncode}: "
        f"{result.stdout}{result.stderr}"
    )
    if expected_count == 1:
        pr = promotion_pr()
        expected = {
            "merge_sha": "a" * 40, "head_sha": pr["head"]["sha"],
            "pr_number": str(pr["number"]), "pr_url": pr["html_url"],
            "merged_at": pr["merged_at"], "head_repo": "owner/repo",
            "head_is_fork": "false", "association": "MEMBER",
            "pr_author": "member", "merged_by": "writer", "ok": "true",
        }
        assert result.stdout == ""
    else:
        expected = {"ok": "false"}
        assert result.stdout == (
            f"::notice::found {expected_count} merged PRs with merge commit "
            f"{'a' * 40} and base main; skipping\n"
        ), f"resolution must defer {expected_count} matches; got {result.stdout!r}"
    assert outputs == expected, f"resolved outputs differ: {outputs}"
    assert result.stderr == "" and summary == ""
    expected_calls = [
        f"api --paginate --slurp repos/owner/repo/commits/{'a' * 40}/pulls?per_page=100",
    ]
    if expected_count == 1:
        expected_calls.append("api repos/owner/repo/pulls/17")
    assert calls == expected_calls, "resolution must read every page and only the unique PR"


@pytest.mark.parametrize("pages,count", [
    pytest.param([[]], 0, id="zero-matches"),
    pytest.param([[promotion_pr()], [promotion_pr(18)]], 2, id="two-pages-two-matches"),
    pytest.param([[promotion_pr()]], 1, id="one-match"),
    pytest.param([[promotion_pr(18, base="release")], [promotion_pr()]], 1, id="later-page-match"),
    pytest.param([[promotion_pr(merge_sha="c" * 40)]], 0, id="wrong-merge"),
    pytest.param([[promotion_pr(base="release")]], 0, id="wrong-base"),
    pytest.param([[promotion_pr(state="open")]], 0, id="open-pr"),
    pytest.param([[promotion_pr(merged_at=None)]], 0, id="unmerged-close"),
])
def test_promotion_push_resolves_exactly_one_merged_main_pr(pages, count, tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_promotion_resolution(workflow, pages, tmp_path, count)


@pytest.mark.parametrize("replacement,pages,count", [
    pytest.param("if len(matches) > 1:", [[]], 0, id="zero-not-deferred"),
    pytest.param("guess-nonmatching-pr", [[promotion_pr(state="open")]], 0,
                 id="zero-guessed-from-nonmatch"),
    pytest.param("if len(matches) < 1:", [[promotion_pr()], [promotion_pr(18)]], 2,
                 id="two-accepted"),
])
def test_promotion_resolver_cardinality_mutations_are_rejected(replacement, pages, count, tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_promotion_resolution(workflow, pages, tmp_path / "control", count)
    original = "if len(matches) != 1:"
    assert workflow.count(original) == 1, "mutation must find the real cardinality guard"
    mutant = workflow.replace(
        original, "if len(matches) > 1:" if replacement == "guess-nonmatching-pr" else replacement, 1,
    )
    if replacement == "guess-nonmatching-pr":
        selection = "selected = matches[0]"
        assert mutant.count(selection) == 1
        mutant = mutant.replace(
            selection, "selected = (matches or [pr for page in pages for pr in page])[0]", 1,
        )
    with pytest.raises(AssertionError, match="push resolution must exit green|resolution must defer [02] matches"):
        assert_promotion_resolution(mutant, pages, tmp_path / "mutant", count)


def test_promotion_entry_propagates_a_deliberate_resolver_crash(tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_promotion_resolution(workflow, [[promotion_pr()]], tmp_path / "control", 1)
    original = "              pages = json.loads("
    assert workflow.count(original) == 1
    mutant = workflow.replace(
        original, '              raise RuntimeError("deliberate resolver crash")\n' + original, 1,
    )
    result, outputs, calls, summary = run_promotion_entry(
        mutant, [[promotion_pr()]], tmp_path / "crash",
    )
    assert result.returncode != 0
    assert "RuntimeError: deliberate resolver crash" in result.stderr
    assert outputs == {} and summary == "" and len(calls) == 1


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_promotion_entry_requires_main_for_push_and_dispatch(event, tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    result, outputs, calls, summary = run_promotion_entry(
        workflow, [[promotion_pr()]], tmp_path,
        overrides={"EVENT": event, "EVENT_NAME": event, "REF": "refs/heads/topic"},
    )
    assert result.returncode == 1
    assert result.stdout == "::error title=authz::baseline promotion requires refs/heads/main\n"
    assert summary == "authz: REFUSED: baseline promotion requires refs/heads/main\n"
    assert outputs == {} and calls == [] and result.stderr == ""


def test_promotion_entry_binds_manual_dispatch_inputs(tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    pr = promotion_pr()
    result, outputs, calls, summary = run_promotion_entry(
        workflow, [], tmp_path, overrides=dispatch_overrides(pr),
    )
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert outputs == {
        "merge_sha": "a" * 40, "head_sha": pr["head"]["sha"],
        "pr_number": str(pr["number"]), "pr_url": pr["html_url"],
        "merged_at": pr["merged_at"], "ok": "true",
    }
    assert calls == [DISPATCH_PR_CALL, DISPATCH_PULLS_CALL] and summary == ""


@pytest.mark.parametrize(
    "case", DISPATCH_REJECT_CASES, ids=[case.name for case in DISPATCH_REJECT_CASES],
)
def test_promotion_entry_rejects_invalid_manual_dispatch_inputs(case, tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    overrides = dispatch_overrides()
    overrides.update(case.changes)
    result, outputs, calls, summary = run_promotion_entry(
        workflow, [], tmp_path, overrides=overrides,
    )
    assert result.returncode == 1
    assert result.stdout == f"::error title=authz::{case.refusal}\n"
    assert summary == f"authz: REFUSED: {case.refusal}\n"
    assert outputs == {} and calls == list(case.expected_calls)
    assert result.stderr == ""


@pytest.mark.parametrize("change", [
    {"number": 18}, {"state": "open"}, {"merged": False},
    {"merge_commit_sha": "c" * 40}, {"base": {"ref": "release"}},
    {"head": {"sha": "c" * 40}}, {"merged_at": "2026-10-02T00:00:00Z"},
])
def test_promotion_resolver_refuses_changed_full_pr_records(change, tmp_path):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    result, outputs, calls, summary = run_promotion_entry(
        workflow, [[promotion_pr()]], tmp_path, full_pr={**promotion_pr(), **change},
    )
    assert result.returncode == 1
    assert result.stdout == "::error title=authz::resolved PR no longer matches the pushed merge\n"
    assert summary == "authz: REFUSED: resolved PR no longer matches the pushed merge\n"
    assert outputs == {} and result.stderr == "" and len(calls) == 2


CODEGEN_GATE_REQUIREMENT = r'''            runs=$(gh api --paginate \
              "repos/${{ github.repository }}/commits/$HEAD_SHA/check-runs?per_page=100")
            gate=$(jq -s '[.[].check_runs[] | select(.name == "gate" and .conclusion == "success")] | length' <<< "$runs")
            statuses=$(gh api --paginate \
              "repos/${{ github.repository }}/commits/$HEAD_SHA/statuses?per_page=100")
            gate_status=$(jq -sr '[.[][] | select(.context == "gate")][0].state // "absent"' <<< "$statuses")
            if { [ "$gate_status" = absent ] && [ "$gate" = "0" ]; } || { [ "$gate_status" != absent ] && [ "$gate_status" != success ]; }; then
              echo "::notice::the required gate context has not succeeded on $HEAD_SHA — not green; skipping"
              echo "green=false" >> "$GITHUB_OUTPUT"; exit 0
            fi
            bad=$(jq -s '[[.[].check_runs[]] | group_by([.app.id, .name])[] | max_by(.id) | select(.conclusion=="failure" or .conclusion=="cancelled" or .conclusion=="timed_out" or .conclusion=="action_required")] | length' <<< "$runs")
            if [ "$bad" != "0" ]; then
              echo "::notice::$bad failing check-run(s) on $HEAD_SHA — not green; skipping"
              echo "green=false" >> "$GITHUB_OUTPUT"; exit 0
            fi
'''


def assert_codegen_promotion_gate(workflow):
    promote = job_blocks(workflow)["promote"]
    step = re.search(
        r"^      - name: Confirm the gate on the PR head\n.*?(?=^      - |\Z)",
        promote, re.M | re.S,
    )
    assert step is not None, "promotion must confirm the gate on the PR head"
    assert "        id: ci\n" in step[0]
    assert "        if: steps.exacttree.outputs.ok == 'true'\n" in step[0]
    assert '          else\n' in step[0] and CODEGEN_GATE_REQUIREMENT in step[0], (
        "codegen must require its gate context and newest check-runs"
    )


def test_promotion_preserves_the_codegen_gate_context():
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_codegen_promotion_gate(workflow)


@pytest.mark.parametrize("mutation", ["removed-step", "missing-context", "old-check-runs"])
def test_codegen_promotion_gate_mutations_are_rejected(mutation):
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_codegen_promotion_gate(workflow)
    if mutation == "removed-step":
        mutant, count = re.subn(
            r"^      - name: Confirm the gate on the PR head\n.*?(?=^      - |\Z)",
            "", workflow, count=1, flags=re.M | re.S,
        )
        assert count == 1
    else:
        original = (
            '[ "$gate_status" != success ]' if mutation == "missing-context"
            else "max_by(.id)"
        )
        assert workflow.count(original) == 1
        mutant = workflow.replace(
            original, '[ "$gate_status" = success ]' if mutation == "missing-context"
            else "min_by(.id)", 1,
        )
    assert mutant != workflow
    with pytest.raises(AssertionError, match="confirm the gate|require its gate context"):
        assert_codegen_promotion_gate(mutant)
