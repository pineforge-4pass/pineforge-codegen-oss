"""Protect workflow concurrency, the trusted gate status and promotion authz."""
import json
import re
import shutil
import subprocess
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
    assert matches, "workflow jobs must use the checked block format"
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
    assert "package-files" in jobs, "test must have an unconditional package-files light job"
    package = jobs["package-files"]
    assert re.search(r"^    needs: \[authz\]$", package, re.M)
    assert re.search(r"^    runs-on: ubuntu-24\.04$", package, re.M)
    assert re.search(r"^    if:", package, re.M) is None
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


# Independent literal of the SEC846 promotion variant of ci-labels.md's authz.
# Copy the engine reference, including its header, comments and blank separator.
# Its nested author case is verbatim canon; never derive this from the workflow.
PROMOTE_GUARD = '''  promote-guard:
    name: promote-guard
    runs-on: ubuntu-latest
    permissions: {}
    steps:
      - name: Only our own merged pull requests and dispatches promote the baseline
        env:
          EVENT: ${{ github.event_name }}
          REPO: ${{ github.repository }}
          HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}
          HEAD_IS_FORK: ${{ github.event.pull_request.head.repo.fork }}
          ASSOCIATION: ${{ github.event.pull_request.author_association }}
          PR_AUTHOR: ${{ github.event.pull_request.user.login }}
          MERGED: ${{ github.event.pull_request.merged }}
          MERGED_BY: ${{ github.event.pull_request.merged_by.login }}
          GH_TOKEN: ${{ github.token }}   # read-only use: permission lookups for the PR author and merger
          # Bot logins whose same-repository pull requests count as ours: dependabot[bot] (CI-DEPBOT A)
          # and the org's own GitHub App bot logins by name (space-separated), nothing else.
          ALLOWED_BOTS: "dependabot[bot] pineforge-app[bot]"
        run: |
          set -eu
          fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }
          case "$EVENT" in
            workflow_dispatch)
              # Only someone with write access to this repository (or our App's token) can cause these
              # events; none of them runs pull-request code.
              ;;
            pull_request_target)
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
              esac
              # Only our own authorised unmerged closes pass without promotion.
              [ "$MERGED" = "true" ] || { echo "promote-guard: unmerged pull request; promotion is skipped" >> "$GITHUB_STEP_SUMMARY"; exit 0; }
              [ -n "$MERGED_BY" ] || fail "pull_request_target merger is empty"
              case " $ALLOWED_BOTS " in
                *" $MERGED_BY "*) ;;
                *)
                  # No author_association exists for the merger: ask GitHub for the merger's permission on
                  # this repository; anything but admin/maintain/write (or a failed call) refuses.
                  perm=$(gh api "repos/$REPO/collaborators/$MERGED_BY/permission" --jq .permission 2>/dev/null || echo none)
                  case "$perm" in admin|maintain|write) ;; *) fail "pull_request_target merger ${MERGED_BY:-unknown} has permission '$perm', not write" ;; esac ;;
              esac ;;
            *) fail "event $EVENT does not run baseline promotion (only pull_request_target and workflow_dispatch are allowed)" ;;
          esac
          echo "authz: allowed ($EVENT${PR_AUTHOR:+ by $PR_AUTHOR})" >> "$GITHUB_STEP_SUMMARY"

'''


def assert_promotion_guard(workflow):
    trigger = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.findall(r"^  ([a-z_]+):", trigger, re.M) == [
        "pull_request_target", "workflow_dispatch",
    ], "promotion accepts only closed pull_request_target and manual dispatch"
    assert "  pull_request_target:\n    types: [closed]\n" in trigger
    jobs = job_blocks(workflow)
    assert tuple(jobs) == ("promote-guard", "promote"), "promotion must start with its guard"
    guard = jobs["promote-guard"]
    assert "secrets." not in guard, "promotion guard must not receive secrets"
    assert "checkout" not in guard.lower(), "promotion guard must not check out code"
    assert "id-token" not in guard, "promotion guard must not request an OIDC token"
    assert re.findall(r"^    permissions: ([^\n]+)$", guard, re.M) == ["{}"], (
        "promotion guard must have empty job permissions"
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
        "    if: ${{ github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true }}\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
    ), "credential job must depend on the guard without a status-function bypass"
    for key in ("needs", "if", "runs-on", "steps"):
        assert len(re.findall(rf"^    {key}:", promote, re.M)) == 1, (
            f"credential job must not override {key}"
        )


def test_promote_baseline_requires_trusted_head_author_and_merger():
    workflow = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
    assert_promotion_guard(workflow)


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
        "fork-head-unmerged",
        {"HEAD_REPO": "outsider/fork", "HEAD_IS_FORK": "true",
         "MERGED": "false", "ASSOCIATION": "NONE", "MERGED_BY": ""},
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
        "same-repo-fork-flag-unmerged",
        {"HEAD_IS_FORK": "true", "MERGED": "false"},
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
        "none-author-unmerged",
        {"ASSOCIATION": "NONE", "PR_AUTHOR": "outsider", "MERGED": "false"},
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
        1, "author unknown (NONE) has permission 'none', not write, and is not an allowlisted bot",
        author_lookup=True,
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
        1, "pull_request_target merger writer has permission 'read', not write",
        author_permission="write", author_lookup=True,
        merger_permission="read", merger_lookup=True,
    ),
    PromotionCase(
        "own-unmerged-contributor-with-write",
        {"ASSOCIATION": "CONTRIBUTOR", "PR_AUTHOR": "agent",
         "MERGED": "false", "MERGED_BY": ""},
        0, author_permission="write", author_lookup=True,
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
        1, "pull_request_target merger writer has permission 'read', not write",
        merger_permission="read", merger_lookup=True,
    ),
    PromotionCase(
        "merger-triage-permission", {},
        1, "pull_request_target merger writer has permission 'triage', not write",
        merger_permission="triage", merger_lookup=True,
    ),
    PromotionCase(
        "merger-lookup-fails", {},
        1, "pull_request_target merger writer has permission 'none', not write",
        merger_permission="", merger_lookup_exit=1, merger_lookup=True,
    ),
    PromotionCase(
        "merger-lookup-fails-with-write-output", {},
        1, "pull_request_target merger writer has permission 'write\nnone', not write",
        merger_permission="write", merger_lookup_exit=1, merger_lookup=True,
    ),
    PromotionCase(
        "merger-permission-empty", {},
        1, "pull_request_target merger writer has permission '', not write",
        merger_permission="", merger_lookup=True,
    ),
    PromotionCase(
        "empty-merger", {"MERGED_BY": ""},
        1, "pull_request_target merger is empty",
    ),
    PromotionCase(
        "own-unmerged-member-close", {"MERGED": "false", "MERGED_BY": ""}, 0,
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
         "ASSOCIATION": "", "PR_AUTHOR": "", "MERGED": "", "MERGED_BY": ""},
        0,
    ),
    PromotionCase(
        "push", {"EVENT": "push"},
        1, "event push does not run baseline promotion (only pull_request_target and workflow_dispatch are allowed)",
    ),
    PromotionCase(
        "workflow-run", {"EVENT": "workflow_run"},
        1, "event workflow_run does not run baseline promotion (only pull_request_target and workflow_dispatch are allowed)",
    ),
    PromotionCase(
        "unknown-event", {"EVENT": "unexpected"},
        1, "event unexpected does not run baseline promotion (only pull_request_target and workflow_dispatch are allowed)",
    ),
]


def promotion_guard_script(workflow):
    """Extract the real guard's sole block-style run script, without a copy."""
    guard = job_blocks(workflow)["promote-guard"]
    scripts = re.findall(r"^        run: \|\n((?:          [^\n]*\n|\n)+)", guard, re.M)
    assert len(scripts) == 1, "promotion guard must have exactly one inline run script"
    return textwrap.dedent(scripts[0])


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
    env = {
        "EVENT": "pull_request_target",
        "REPO": "pineforge-4pass/pineforge-codegen-oss",
        "HEAD_REPO": "pineforge-4pass/pineforge-codegen-oss",
        "HEAD_IS_FORK": "false",
        "ASSOCIATION": "MEMBER",
        "PR_AUTHOR": "member",
        "MERGED": "true",
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
    elif env["EVENT"] == "pull_request_target" and env["MERGED"] != "true":
        assert summary == "promote-guard: unmerged pull request; promotion is skipped\n", case.name
        assert result.stdout == "", case.name
    else:
        author = f" by {env['PR_AUTHOR']}" if env["PR_AUTHOR"] else ""
        assert summary == f"authz: allowed ({env['EVENT']}{author})\n", case.name
        assert result.stdout == "", case.name
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
    case = next(case for case in PROMOTION_CASES if case.name == "fork-head-unmerged")
    with pytest.raises(AssertionError, match=case.name):
        assert_promotion_guard_case(mutant, case, tmp_path)


@pytest.mark.parametrize("original,replacement,case_name", [
    pytest.param(
        'fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }',
        "fail() { :; }", "fork-head-unmerged", id="no-op-fail",
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
        '              [ -n "$MERGED_BY" ] || fail "pull_request_target merger is empty"\n',
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
    "MERGED", "MERGED_BY", "GH_TOKEN", "ALLOWED_BOTS",
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
        '          echo "authz: allowed ($EVENT${PR_AUTHOR:+ by $PR_AUTHOR})" >> "$GITHUB_STEP_SUMMARY"\n\n  promote:\n',
        '          echo "authz: allowed ($EVENT${PR_AUTHOR:+ by $PR_AUTHOR})" >> "$GITHUB_STEP_SUMMARY"\n  promote:\n',
        id="guard-separator-removed",
    ),
    pytest.param(
        'fail() { echo "::error title=authz::$1"; echo "authz: REFUSED: $1" >> "$GITHUB_STEP_SUMMARY"; exit 1; }',
        "fail() { :; }",
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
        '''case "$perm" in admin|maintain|write) ;; *) fail "pull_request_target merger''',
        '''case "$perm" in admin|maintain|write|read|none) ;; *) fail "pull_request_target merger''',
        id="non-write-merger-accepted",
    ),
    pytest.param(
        '              [ "$MERGED" = "true" ] || {',
        '              [ "$MERGED" = "false" ] || {',
        id="merged-checks-bypassed",
    ),
    pytest.param(
        '            *) fail "event $EVENT does not run baseline promotion (only pull_request_target and workflow_dispatch are allowed)" ;;\n',
        "            *) ;;\n",
        id="unknown-event-accepted",
    ),
    pytest.param(
        "            workflow_dispatch)\n",
        "            push|workflow_dispatch)\n",
        id="extra-event-accepted",
    ),
    pytest.param(
        "    permissions: {}\n",
        "",
        id="guard-inherits-oidc-permissions",
    ),
    pytest.param(
        "    permissions: {}\n",
        "    permissions:\n      id-token: write\n",
        id="guard-requests-oidc-token",
    ),
    pytest.param(
        '              [ -n "$MERGED_BY" ] || fail "pull_request_target merger is empty"\n',
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
        "    if: ${{ github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true }}\n",
        "    if: ${{ always() && (github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true) }}\n",
        id="credential-job-bypasses-failed-guard",
    ),
    pytest.param(
        "    runs-on: ubuntu-latest\n    steps:\n",
        "    runs-on: unreviewed-runner\n    steps:\n",
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
        "  pull_request_target:\n    types: [closed]\n",
        "  pull_request_target:\n    types: [opened]\n",
        id="trigger-no-longer-closed",
    ),
    pytest.param(
        "  workflow_dispatch:\n    inputs:\n",
        "  workflow_call:\n    inputs:\n",
        id="dispatch-trigger-replaced",
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
